from pydantic import BaseModel, Field


class ReadFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    path: str = "."
    limit: int = Field(default=50, ge=1, le=200)