import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="G-Cal Wrapper API")

frontend_url = os.getenv("FRONTEND_URL", "http://localhost:4200")
redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

CLIENT_CONFIG = {
    "web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [redirect_uri],
    }
}

SESSIONS: Dict[str, Dict] = {}
OAUTH_STATE_CODE_VERIFIERS: Dict[str, str] = {}


class EventBase(BaseModel):
    title: str
    description: Optional[str] = None
    start: datetime
    end: datetime
    location: Optional[str] = None


class EventCreate(EventBase):
    attendees: Optional[List[str]] = None


class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    location: Optional[str] = None


class Event(EventBase):
    id: str


class AuthStatus(BaseModel):
    authenticated: bool
    email: Optional[str] = None


def to_event(item: Dict) -> Event:
    start_value = item.get("start", {}).get("dateTime") or item.get("start", {}).get(
        "date"
    )
    end_value = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
    if not start_value or not end_value:
        raise HTTPException(status_code=500, detail="Invalid event payload from Google")

    start_dt = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_value.replace("Z", "+00:00"))

    return Event(
        id=item["id"],
        title=item.get("summary", "(No title)"),
        description=item.get("description"),
        start=start_dt,
        end=end_dt,
        location=item.get("location"),
    )


def get_credentials_from_request(request: Request) -> Credentials:
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in SESSIONS:
        raise HTTPException(status_code=401, detail="Not authenticated")

    stored = SESSIONS[session_id]
    credentials = Credentials(
        token=stored.get("token"),
        refresh_token=stored.get("refresh_token"),
        token_uri=stored.get("token_uri"),
        client_id=stored.get("client_id"),
        client_secret=stored.get("client_secret"),
        scopes=stored.get("scopes"),
    )

    if credentials.expired and credentials.refresh_token:
        from google.auth.transport.requests import Request as GoogleRequest

        credentials.refresh(GoogleRequest())
        SESSIONS[session_id] = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }

    return credentials


def build_calendar_service(request: Request):
    credentials = get_credentials_from_request(request)
    return build("calendar", "v3", credentials=credentials)


@app.get("/")
async def root():
    return {"message": "G-Cal Wrapper API is running"}


@app.get("/auth/login")
async def login():
    if (
        not CLIENT_CONFIG["web"]["client_id"]
        or not CLIENT_CONFIG["web"]["client_secret"]
    ):
        raise HTTPException(
            status_code=500, detail="Google Client ID or Secret not configured in .env"
        )

    flow = Flow.from_client_config(
        CLIENT_CONFIG, scopes=SCOPES, redirect_uri=redirect_uri
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true"
    )
    if not flow.code_verifier:
        raise HTTPException(
            status_code=500, detail="Failed to initialize PKCE verifier"
        )
    OAUTH_STATE_CODE_VERIFIERS[state] = flow.code_verifier
    return RedirectResponse(authorization_url)


@app.get("/auth/callback")
async def callback(code: str, state: Optional[str] = None):
    if not state:
        raise HTTPException(status_code=400, detail="Missing OAuth state")

    code_verifier = OAUTH_STATE_CODE_VERIFIERS.get(state)
    if not code_verifier:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    flow = Flow.from_client_config(
        CLIENT_CONFIG, scopes=SCOPES, redirect_uri=redirect_uri
    )
    flow.code_verifier = code_verifier

    try:
        flow.fetch_token(code=code)
        credentials = flow.credentials
        session_id = str(uuid.uuid4())
        SESSIONS[session_id] = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }

        redirect = RedirectResponse(f"{frontend_url}/?auth=success")
        redirect.set_cookie("session_id", session_id, httponly=True, samesite="lax")
        OAUTH_STATE_CODE_VERIFIERS.pop(state, None)
        return redirect
    except Exception as exc:
        OAUTH_STATE_CODE_VERIFIERS.pop(state, None)
        raise HTTPException(
            status_code=400, detail=f"Failed to fetch token: {str(exc)}"
        )


@app.get("/auth/me", response_model=AuthStatus)
async def auth_me(request: Request):
    try:
        credentials = get_credentials_from_request(request)
        oauth_service = build("oauth2", "v2", credentials=credentials)
        profile = oauth_service.userinfo().get().execute()
        return AuthStatus(authenticated=True, email=profile.get("email"))
    except Exception:
        return AuthStatus(authenticated=False)


@app.post("/auth/logout")
async def logout(request: Request, response: Response):
    session_id = request.cookies.get("session_id")
    if session_id:
        SESSIONS.pop(session_id, None)
    response.delete_cookie("session_id")
    return {"ok": True}


@app.get("/api/v1/events", response_model=List[Event])
async def get_events(request: Request):
    try:
        service = build_calendar_service(request)
        result = (
            service.events()
            .list(
                calendarId="primary",
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
            )
            .execute()
        )
        items = result.get("items", [])
        return [to_event(item) for item in items if item.get("status") != "cancelled"]
    except HttpError as exc:
        raise HTTPException(
            status_code=400, detail=f"Google Calendar error: {exc.reason}"
        )


@app.post("/api/v1/events", response_model=Event)
async def create_event(request: Request, event: EventCreate):
    try:
        service = build_calendar_service(request)
        payload = {
            "summary": event.title,
            "description": event.description,
            "location": event.location,
            "start": {"dateTime": event.start.isoformat()},
            "end": {"dateTime": event.end.isoformat()},
        }

        if event.attendees:
            payload["attendees"] = [{"email": email} for email in event.attendees]

        created = service.events().insert(calendarId="primary", body=payload).execute()
        return to_event(created)
    except HttpError as exc:
        raise HTTPException(
            status_code=400, detail=f"Google Calendar error: {exc.reason}"
        )


@app.patch("/api/v1/events/{event_id}", response_model=Event)
async def update_event(request: Request, event_id: str, event: EventUpdate):
    try:
        service = build_calendar_service(request)
        current = service.events().get(calendarId="primary", eventId=event_id).execute()

        if event.title is not None:
            current["summary"] = event.title
        if event.description is not None:
            current["description"] = event.description
        if event.location is not None:
            current["location"] = event.location
        if event.start is not None:
            current["start"] = {"dateTime": event.start.isoformat()}
        if event.end is not None:
            current["end"] = {"dateTime": event.end.isoformat()}

        updated = (
            service.events()
            .update(calendarId="primary", eventId=event_id, body=current)
            .execute()
        )
        return to_event(updated)
    except HttpError as exc:
        raise HTTPException(
            status_code=400, detail=f"Google Calendar error: {exc.reason}"
        )


@app.delete("/api/v1/events/{event_id}")
async def delete_event(request: Request, event_id: str):
    try:
        service = build_calendar_service(request)
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return {"ok": True}
    except HttpError as exc:
        raise HTTPException(
            status_code=400, detail=f"Google Calendar error: {exc.reason}"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
