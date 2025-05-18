# g4f_wrapper_simple.py
import asyncio, g4f
from tenacity import retry, stop_after_attempt, wait_exponential

# ---- tweak these if you like ---------------------------------------------
_ATTEMPTS  = 3           # retry this many times
_MIN_WAIT  = 2           # back‑off starting at 2 s
_MAX_WAIT  = 10          # …up to 10 s
# --------------------------------------------------------------------------

@retry(stop=stop_after_attempt(_ATTEMPTS),
       wait=wait_exponential(multiplier=1, min=_MIN_WAIT, max=_MAX_WAIT))
async def g4f_chat_async(
    messages,
    *,
    providers=None,
    model=None,
    **kwargs,
):
    """
    PARAMETERS
    ----------
    messages   (required)
        A list like [{'role': 'user', 'content': 'Hello'}]

    providers  (optional)
        • None  – let g4f auto‑pick any working provider (default)      
        • Single provider class, e.g. g4f.Provider.Bing  
        • List/tuple of provider classes – will try them in order

    model      (optional)
        Leave None to let g4f decide, or specify a model string.

    **kwargs   (optional)
        Passed straight to g4f (proxy=..., timeout=..., temperature=...)

    RETURNS
    -------
    The assistant’s plain‑text reply (str).
    """
    # Normalise providers argument into an ordered list
    if providers is None:
        prov_list = [None]                         # auto‑select
    elif isinstance(providers, (list, tuple)):
        prov_list = list(providers)
    else:
        prov_list = [providers]

    last_err = None
    for prov in prov_list:
        try:
            return await g4f.ChatCompletion.create_async(
                model=model or g4f.models.default,
                messages=list(messages),          # g4f needs a real list
                provider=prov,                    # None → auto provider
                **kwargs,
            )
        except Exception as err:
            last_err = err                        # remember & try next
    # All attempts failed
    raise last_err

def g4f_chat(messages, **kwargs):
    """Sync convenience wrapper around g4f_chat_async()."""
    return asyncio.run(g4f_chat_async(messages, **kwargs))

# quick test:  python g4f_wrapper_simple.py "Hi"
if __name__ == "__main__":
    import sys
    prompt = " ".join(sys.argv[1:]) or "Hello"
    print(g4f_chat([{"role": "user", "content": prompt}]))
