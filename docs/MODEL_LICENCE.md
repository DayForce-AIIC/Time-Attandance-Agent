# Model licence notes

The default configuration uses a deterministic mock provider and downloads no model weights. Ollama is optional and is not itself a model license.

| Model/provider | License | Source | Acceptable-use and distribution notes | Checked |
|---|---|---|---|---|
| `mock` provider | No model weights; not applicable | [`backend/app/llm/mock.py`](../backend/app/llm/mock.py) | Synthetic deterministic output for tests and demos. | Not applicable |
| Qwen2.5-7B-Instruct (`qwen2.5:7b-instruct`, optional Ollama model) | Apache License 2.0, as identified by the upstream repository | [Qwen model card](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct) · [upstream LICENSE](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/main/LICENSE) | Review the exact model artifact and its license notices before redistribution. Comply with the license and applicable law; validate outputs and keep manager approval and deterministic checks in place. The model is not bundled with this repository. | 2026-11-03 |

The upstream model repository labels Qwen2.5-7B-Instruct as `apache-2.0`. Check the current model repository, downloaded artifact, and Ollama distribution terms again before a public release or commercial deployment. This note records the prototype's dependency choice; it is not legal advice.
