"""FastAPI app factory: one process, OCR jobs on an in-process single-thread executor (run uvicorn with --workers 1)."""

import logging
import os
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from converter.app import routes_conversions, routes_templates
from converter.app.display import is_money_path, peso
from converter.store.db import init_db, recover_interrupted
from converter.store.repositories import NotFoundError
from converter.worker import runner

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).parent
DEFAULT_DATA_DIR = "./data"
DB_FILE_NAME = "converter.db"
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


def _configure_package_logging() -> None:
    # Uvicorn configures only its own loggers; without a handler the per-stage OCR timings are dropped.
    package_logger = logging.getLogger("converter")
    if not package_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
        package_logger.addHandler(handler)
        package_logger.setLevel(logging.INFO)


def _single_ocr_thread() -> Executor:
    # One OCR job at a time: measured contention between parallel OCR runs, not CPU count, was the bottleneck.
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")


def create_app(data_dir: Path | None = None, executor_factory: Callable[[], Executor] = _single_ocr_thread) -> FastAPI:
    resolved_data_dir = Path(data_dir or os.environ.get("CONVERTER_DATA_DIR", DEFAULT_DATA_DIR)).resolve()
    db_path = resolved_data_dir / DB_FILE_NAME

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _configure_package_logging()
        resolved_data_dir.mkdir(parents=True, exist_ok=True)
        init_db(db_path)
        recovered = recover_interrupted(db_path)
        if recovered:
            log.warning("marked %s interrupted conversion(s) as failed", recovered)
        runner.reset_stop_request()
        app.state.executor = executor_factory()
        try:
            yield
        finally:
            # Without this, shutdown would wait for every remaining page of a running batch.
            runner.request_stop()
            app.state.executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="Order Converter", lifespan=lifespan)
    app.state.data_dir = resolved_data_dir
    app.state.db_path = db_path
    app.state.templates = Jinja2Templates(directory=APP_DIR / "templates")
    app.state.templates.env.filters["peso"] = peso
    app.state.templates.env.globals["is_money_path"] = is_money_path
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    app.include_router(routes_conversions.router)
    app.include_router(routes_templates.router)

    @app.exception_handler(NotFoundError)
    def not_found(request: Request, exc: NotFoundError) -> PlainTextResponse:
        return PlainTextResponse("Not found", status_code=404)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    return app


app = create_app()
