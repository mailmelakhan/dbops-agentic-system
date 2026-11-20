"""Process entry point: builds every collaborator from the environment (uvicorn --factory)."""

import os
from functools import partial

import boto3
import yaml
from fastapi import FastAPI

from agent_api.app import create_app
from agent_api.mcp import aws_tools_provider
from agent_api.mock_client import MockClient
from agent_api.proposals import Proposer
from agent_api.runner import Runner
from agent_api.scope import ScopeResolver
from agent_api.settings import load_settings
from agent_api.store import Store


def build_from_env() -> FastAPI:
    settings = load_settings(os.environ)
    store = Store(settings.database_url)
    rds = partial(boto3.client, "rds")  # boto3 clients are created per region on first use, not at build time

    def rds_client(region: str):
        return rds(region_name=region)

    scope = ScopeResolver(rds_client, settings.aws_regions, settings.scope_tag_key, settings.scope_tag_value)
    mock = MockClient(settings.mock_backend_url, settings.service_token)
    prices = yaml.safe_load(settings.prices_file.read_text()) or {}
    proposer = Proposer(scope, rds_client, mock, prices)
    runner = Runner(settings, store, scope, proposer, mock, aws_tools_provider(settings))
    return create_app(settings, store, runner)
