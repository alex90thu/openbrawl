from typing import Any, Optional

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    nickname: str
    avatar_key: Optional[str] = None
    avatar_filename: Optional[str] = None
    avatar_base64: Optional[str] = None


class ActionSubmit(BaseModel):
    action: str
    speech_as: Optional[str] = None
    speech_content: Optional[str] = None
    gambling: Optional[Any] = None


class SpeechSubmit(BaseModel):
    speech_as: Optional[str] = None
    speech_content: str


class NicknameUpdateRequest(BaseModel):
    player_id: str
    secret_token: str
    new_nickname: str


class AvatarUpdateRequest(BaseModel):
    player_id: str
    secret_token: str
    avatar_base64: str
    avatar_filename: Optional[str] = None
    avatar_key: Optional[str] = None


class FeatureEventRequest(BaseModel):
    event_type: str
    player_id: Optional[str] = None
    round_id: Optional[int] = None
    payload: dict = Field(default_factory=dict)
