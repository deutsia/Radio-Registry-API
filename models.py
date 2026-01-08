"""
Pydantic models for request/response validation
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator, HttpUrl
import re


class StationBase(BaseModel):
    """Base station fields for creation/update"""
    name: str = Field(..., min_length=1, max_length=100)
    stream_url: str = Field(..., max_length=500)
    homepage: Optional[str] = Field(None, max_length=500)
    genre: str = Field("", max_length=200)  # Allow longer for comma-separated multi-genre
    codec: Optional[str] = Field(None, max_length=20)
    bitrate: Optional[int] = Field(None, ge=8, le=1024)
    language: str = Field("", max_length=50)

    @field_validator('stream_url')
    @classmethod
    def validate_stream_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(('http://', 'https://')):
            raise ValueError('URL must start with http:// or https://')
        # Must be .onion or .b32.i2p (no clearnet, no regular .i2p)
        if not ('.onion' in v or '.b32.i2p' in v):
            raise ValueError('URL must be a .onion or .b32.i2p address')
        return v

    @field_validator('homepage')
    @classmethod
    def validate_homepage(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v.strip() == '':
            return None
        v = v.strip()
        if not v.startswith(('http://', 'https://')):
            raise ValueError('Homepage must start with http:// or https://')
        # Must be .onion or .b32.i2p (no clearnet)
        if not ('.onion' in v or '.b32.i2p' in v):
            raise ValueError('Homepage must be a .onion or .b32.i2p address')
        return v

    @field_validator('codec')
    @classmethod
    def validate_codec(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.upper().strip()

    @field_validator('name', 'genre')
    @classmethod
    def strip_whitespace(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip()


class StationSubmit(StationBase):
    """Schema for station submission"""
    network: Optional[str] = Field(None, description="Auto-detected from URL if not provided")
    favicon_url: Optional[str] = Field(None, max_length=500, description="Cover art URL (will be mirrored locally)")

    @field_validator('network')
    @classmethod
    def validate_network(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.lower().strip()
        if v not in ('tor', 'i2p'):
            raise ValueError('Network must be "tor" or "i2p"')
        return v

    @field_validator('favicon_url')
    @classmethod
    def validate_favicon_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v.strip() == '':
            return None
        v = v.strip()
        if not v.startswith(('http://', 'https://')):
            raise ValueError('Cover art URL must start with http:// or https://')
        return v


class StationResponse(BaseModel):
    """Station response schema (matches what Deutsia Radio expects)"""
    id: str
    name: str
    streamUrl: str
    homepage: Optional[str] = None
    faviconUrl: Optional[str] = None
    genre: str
    codec: Optional[str] = None
    bitrate: Optional[int] = None
    language: str = "Unknown"
    network: str
    lastCheckOk: bool
    lastCheckTime: Optional[str] = None
    healthStatus: str = "unknown"  # "online", "offline", or "dead"
    consecutiveFailures: int = 0

    class Config:
        from_attributes = True


class StationListResponse(BaseModel):
    """Response for station list endpoint"""
    stations: list[StationResponse]
    total: int
    online: int


class StationDetailResponse(StationResponse):
    """Extended station details"""
    status: str
    checkCount: int = 0
    checkOkCount: int = 0
    consecutiveFailures: int = 0
    lastOnlineTime: Optional[str] = None
    submittedAt: Optional[str] = None
    approvedAt: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class SubmitResponse(BaseModel):
    """Response after submitting a station"""
    success: bool
    message: str
    station_id: Optional[str] = None


class StatsResponse(BaseModel):
    """Directory statistics"""
    total_stations: int
    online_stations: int
    offline_stations: int = 0
    dead_stations: int = 0
    tor_stations: int
    i2p_stations: int
    pending_submissions: int
    last_health_check: Optional[str] = None


class HealthResponse(BaseModel):
    """API health check response"""
    status: str
    database: bool
    timestamp: str


class ErrorResponse(BaseModel):
    """Error response"""
    error: str
    detail: Optional[str] = None
