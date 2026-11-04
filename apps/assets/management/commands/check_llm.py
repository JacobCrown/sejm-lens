from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.assets.llm import LLMClient, LLMError, extract_json


class Command(BaseCommand):
    help = "Checks the LLM provider from .env with one short request."

    def handle(self, *args, **opts):
        try:
            client = LLMClient.from_settings()
        except LLMError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Provider: {settings.LLM_PROVIDER or '(custom)'} · URL: {client.url}\n"
            f"Model: {client.model} · response_format: {client.response_format}"
        )
        try:
            response = client.chat([
                {"role": "system", "content": "Reply with a JSON object only."},
                {"role": "user", "content": 'Return {"ok": true, "number": 2+2 computed}.'},
            ])
            data = extract_json(response.text)
        except (LLMError, ValueError) as exc:
            raise CommandError(f"Error: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"OK ({response.duration_ms} ms): {data}"))
        if response.dropped_params:
            self.stdout.write(self.style.WARNING(
                f"The provider does not support: {', '.join(response.dropped_params)}. "
                "Leave them empty in .env to avoid the retries."
            ))
        if response.prompt_tokens is not None:
            self.stdout.write(f"Tokens: {response.prompt_tokens} + {response.completion_tokens}")
