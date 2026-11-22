from pydantic import BaseModel, ConfigDict, Field, field_validator


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListFilesArgs(ToolArguments):
    path: str = Field(default=".", min_length=1, max_length=500)
    limit: int = Field(default=200, ge=1, le=200)

    @field_validator("path", mode="before")
    @classmethod
    def normalize_empty_path(cls, value):
        return "." if value == "" else value

class ReadFileArgs(ToolArguments):
    path: str = Field(min_length=1, max_length=500)


class SearchCodeArgs(ToolArguments):
    query: str = Field(min_length=1, max_length=500)
    path: str = Field(default=".", min_length=1, max_length=500)
    limit: int = Field(default=50, ge=1, le=200)

class WriteFileArgs(ToolArguments):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=1_000_000)
    overwrite: bool = False


class RunWorkspaceChecksArgs(ToolArguments):
    pass