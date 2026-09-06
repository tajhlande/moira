"""Central defaults for inference requests.

``DEFAULT_TEMPERATURE`` and ``DEFAULT_INTELLIGENCE_EXTRA_BODY`` form the
sampling profile for *intelligence model* calls. Workflow nodes pass both
explicitly on every ``chat_completion`` call, so the profile is visible at
each call site.

The profile deliberately does NOT apply to the task model (title
generation, tool description enrichment) or the eval judge — those calls
send only ``temperature`` and rely on the inference server's defaults for
everything else.

``temperature`` lives as its own constant rather than inside
``DEFAULT_INTELLIGENCE_EXTRA_BODY`` because it is a first-class parameter
of ``chat_completion`` with per-call overrides in several nodes (retry and
repair paths lower it).

The specific values for ``top_p``, ``top_k``, ``min_p``,
``presence_penalty``, and ``repetition_penalty`` below are the
recommended general inference parameters for 
Qwen3.5 architecture family models. 

"""

DEFAULT_TEMPERATURE = 1.0

DEFAULT_INTELLIGENCE_EXTRA_BODY: dict[str, float] = {
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 0.0,
    "repetition_penalty": 1.0,
}
