"""Shared request helpers: app state access, page/fragment rendering, safe redirects."""

from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.datastructures import FormData


def db_path(request: Request) -> Path:
    return request.app.state.db_path


def data_dir(request: Request) -> Path:
    return request.app.state.data_dir


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def render(request: Request, name: str, context: dict, status_code: int = 200, headers: dict | None = None):
    return request.app.state.templates.TemplateResponse(
        request, name, context, status_code=status_code, headers=headers
    )


def render_fragments(
    request: Request, parts: list[tuple[str, dict]], status_code: int = 200, headers: dict | None = None
) -> HTMLResponse:
    """Concatenate template fragments into one htmx response (main swap first, then hx-swap-oob parts)."""
    environment = request.app.state.templates.env
    html = "".join(environment.get_template(name).render(context) for name, context in parts)
    return HTMLResponse(html, status_code=status_code, headers=headers)


def redirect(url: str) -> RedirectResponse:
    # 303 so the browser follows a form POST with a GET.
    return RedirectResponse(url, status_code=303)


def safe_next(next_url: str | None) -> str | None:
    """Only same-site absolute paths; anything else (other hosts, schemes, "//host") is dropped."""
    # if not next_url or not next_url.startswith("/") or next_url.startswith(("//", "/\\")):
    #     return None
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return None
    # Browsers drop tab/newline and read "\" as "/" in hrefs, so "/\t/host" or "/\\host" would render as "//host".
    if any(char <= " " or char in "\\\x7f" for char in next_url):
        return None
    return next_url


async def submitted_form(request: Request) -> FormData:
    """Dependency for handlers with dynamic field names; awaiting the body is non-blocking, the handler stays def."""
    return await request.form()
