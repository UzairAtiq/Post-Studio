"""Groq LLM text generation service for crafting LinkedIn posts."""

import logging
from groq import Groq
from langfuse import observe
from config.settings import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger("groq_service")


@observe(name="generate_linkedin_post")
def generate_linkedin_post_content(
    topic: str = "modern software engineering and AI agents",
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> str:
    """Generate high-impact LinkedIn post text using Groq or any OpenAI-compatible LLM API.

    Error Paradigm:
        RAISES EXCEPTIONS. This service follows an exception-raising error style;
        any configuration, API, rate limit, or generation failures raise exceptions
        directly to the caller. It never returns error dictionaries.

    Purpose:
        Calls the Groq or OpenAI-compatible completions endpoint to craft engaging, professional
        LinkedIn copy without emoji embellishments according to system prompts.

    Steps:
        1. Resolve active API key, base URL, and model from custom arguments or settings.
        2. Instantiate either the OpenAI or Groq client with resolved configuration.
        3. Define system persona and format constraints (concise, no emojis, 3-5 hashtags).
        4. Send chat completion request to configured LLM model.
        5. Extract response text and validate non-emptiness.
        6. Return stripped post text string.

    Returns:
        str: Clean generated post content ready for human review.

    Raises:
        ValueError: If neither a custom API key nor GROQ_API_KEY is configured.
        RuntimeError: If LLM API request fails, times out, or returns empty text.
    """
    effective_key = (api_key and api_key.strip()) or GROQ_API_KEY
    if not effective_key:
        raise ValueError("GROQ_API_KEY is not set. Please provide a custom API key or set it in the .env file.")

    effective_base_url = (base_url and base_url.strip()) or None
    effective_model = (model and model.strip()) or GROQ_MODEL

    # define persona and formatting rules for post generation
    system_prompt = (
        "You are an expert LinkedIn content creator and tech thought leader. "
        "Write an engaging, insightful, and professional LinkedIn post on the requested topic. "
        "Include a strong hook, concise paragraphs or bullet points, a thought-provoking takeaway, "
        "and 3-5 relevant hashtags. Do not include markdown meta-text or commentary outside the post itself. "
        "Do not include any emojis in the text."
    )
    user_prompt = f"Create a compelling LinkedIn post about: {topic}"

    try:
        if effective_base_url or (api_key and api_key.strip() != GROQ_API_KEY):
            from openai import OpenAI

            client = OpenAI(
                api_key=effective_key,
                base_url=effective_base_url or "https://api.openai.com/v1",
            )
        else:
            client = Groq(api_key=effective_key)

        # request chat completion from LLM API
        completion = client.chat.completions.create(
            model=effective_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=1024,
        )

        # extract generated message content
        post_text = completion.choices[0].message.content
        if not post_text:
            raise RuntimeError("received empty response from LLM")

        return post_text.strip()

    except Exception as err:
        # log failure details and re-raise with original context
        logger.error(f"failed to generate post from LLM: {err}")
        raise RuntimeError(f"LLM generation failed: {err}") from err
