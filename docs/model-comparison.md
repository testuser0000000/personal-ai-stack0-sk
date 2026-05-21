# Model comparison notes

> Running notes from evaluating different LLMs through this stack.
> Updated as I try more models.

## Local (Ollama)

### hermes3:8b
- **Size on disk:** 4.7 GB (Q4_K_M)
- **RAM at idle:** ~5.5 GB
- **First-token latency (CPU, with Hermes Agent's ~19k-token tool schemas):** ~3 min cold, much faster warm
- **Steady-state tokens/sec:** ~10-15 on Intel Core Ultra 9 285H (CPU only, no discrete GPU)
- **Tool calling:** Strong — same vendor (Nous Research) as the Hermes Agent framework, fine-tuned for that exact format.
- **Quality vs Claude:** Significantly weaker on complex reasoning, comparable on short structured outputs.
- **Use case:** Sensitive prompts where staying local matters more than speed.

## Cloud (OpenRouter free tier)

> _To fill in as I try more — initial slot left blank intentionally._

## Cloud (Anthropic via claude.ai)

- **Use case:** Primary daily-driver. This stack lets me compare other
  models against it; it doesn't replace it.
