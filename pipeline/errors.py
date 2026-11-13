"""Errors the pipeline CLI turns into a JSON error line and an exit code."""


class PipelineError(Exception):
    """A failure the CLI reports as JSON. `extra` fields are merged into the error JSON.

    `message` is positional only, so `extra` may hold a field of that name. The CLI never lets `extra`
    replace the `ok` and `error` fields it writes itself.
    """

    exit_code = 1

    def __init__(self, message: str, /, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class EditInvalid(PipelineError):
    """edit.json failed validation. `errors` lists every problem found."""

    exit_code = 2

    def __init__(self, errors: list[str]):
        super().__init__("edit.json is invalid", errors=errors)
        self.errors = errors


class UnsafeDeleteError(PipelineError):
    """A recursive delete was refused because its target path is not safe."""


class AsrTruncatedError(PipelineError):
    """The ASR engine reported a truncated chunk, so the transcript would be incomplete."""
