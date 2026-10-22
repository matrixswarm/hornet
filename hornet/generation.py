"""Bounded model requests to a user-selected chat-completions endpoint."""
import json
import os
from urllib import error, parse, request

from .project import HornetError
from .text import clean_text


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def chat(prompt, model, base_url, timeout=60, max_tokens=2048):
    parsed = parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HornetError("Use an http(s) base URL without credentials, query, or fragment.")
    key = os.environ.get("HORNET_API_KEY", "")
    if key and parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HornetError("Use HTTPS when sending an API key to a non-loopback endpoint.")
    if not model or not model.strip():
        raise HornetError("Set --model or HORNET_MODEL. For an offline outline use --provider template.")
    if not 0 < timeout <= 300 or not 1 <= max_tokens <= 16384:
        raise HornetError("timeout must be 1-300 seconds and max-tokens 1-16384.")
    payload = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                          "stream": False, "max_tokens": max_tokens}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = request.Request(base_url.rstrip("/") + "/chat/completions", data=payload, headers=headers, method="POST")
    try:
        # A user-selected local endpoint should not inherit an unrelated proxy.
        opener = request.build_opener(NoRedirect(), request.ProxyHandler({})) if parsed.hostname in {"localhost", "127.0.0.1", "::1"} else request.build_opener(NoRedirect())
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(5 * 1024 * 1024 + 1)
            if len(raw) > 5 * 1024 * 1024:
                raise HornetError("Model response exceeds the 5 MiB limit.")
        data = json.loads(raw.decode("utf-8"))
        choice = data["choices"][0]
        text = choice["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise HornetError("Model returned no text content.")
        if choice.get("finish_reason") == "length":
            raise HornetError("Model output hit the token limit; increase --max-tokens or shorten the request.")
        return text
    except error.HTTPError as exc:
        raise HornetError("Model endpoint returned HTTP {}. Check endpoint, model, and credentials.".format(exc.code)) from None
    except (error.URLError, TimeoutError, ConnectionError, OSError):
        raise HornetError("Cannot reach the model endpoint within the request limit; check its address and loaded model.") from None
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
        raise HornetError("Model endpoint returned an invalid chat-completions response.") from None


def generate(recipe, topic, style=None, words=None, provider="compatible", model=None, base_url=None, timeout=60, max_tokens=2048):
    params = recipe.get("parameters", {})
    fields = {"topic": topic, "style": style or params.get("style", "conversational"),
              "length_words": words if words is not None else params.get("length_words", 800)}
    if not topic.strip() or not 1 <= fields["length_words"] <= 20000:
        raise HornetError("Provide a nonempty topic and a word count between 1 and 20000.")
    if provider == "template":
        if not recipe.get("template"):
            raise HornetError("This recipe has no offline template.")
        text = recipe["template"].format_map(fields)
        kind = "offline_outline"
    else:
        model = model or os.environ.get("HORNET_MODEL")
        text = chat(recipe["prompt"].format_map(fields), model,
                    base_url or os.environ.get("HORNET_BASE_URL", "http://127.0.0.1:1234/v1"), timeout, max_tokens)
        kind = "model_draft"
    for step in recipe.get("steps", []):
        action = step.get("action") if isinstance(step, dict) else step
        if action == "smash_patterns":
            text = clean_text(text)
    return {"text": text, "kind": kind, "provider": provider, "model": model if provider != "template" else None}
