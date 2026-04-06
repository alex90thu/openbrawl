from typing import Optional

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    nickname: str


class ActionSubmit(BaseModel):
    action: str
    speech_as: Optional[str] = None
    speech_content: Optional[str] = None


class SpeechSubmit(BaseModel):
    speech_as: Optional[str] = None
    speech_content: str


class NicknameUpdateRequest(BaseModel):
    player_id: str
    secret_token: str
    new_nickname: str


class FeatureEventRequest(BaseModel):
    event_type: str
    player_id: Optional[str] = None
    round_id: Optional[int] = None
    payload: dict = Field(default_factory=dict)
