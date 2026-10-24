# Usage report: final full-dataset run

This report covers the run that produced the submitted `output.csv`:

```bash
python3 code/main.py      # 518 requests from dataset/deletion_requests.csv -> output.csv
```

The pipeline is deterministic and rule-based. It makes **no model or API calls**, so all token and cost figures are zero.

## Model and token usage

| Item | Value |
| --- | --- |
| Model providers | None |
| Model names | None |
| Model calls | 0 |
| Input tokens | 0 |
| Output tokens | 0 |
| Total tokens | 0 |
| Requests processed | 518 |
| Average tokens per request | 0 (0 / 518) |
| Estimated total cost | $0.00 |
| Estimated cost per request | $0.00 |

### Per-model and overall totals

| Provider | Model | Calls | Input tokens | Output tokens | Total tokens | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| (none) | (none) | 0 | 0 | 0 | 0 | $0.00 |
| **Overall** | | **0** | **0** | **0** | **0** | **$0.00** |

## How "no model calls" was verified

- **Imports.** A search of `code/` finds no import of an HTTP, socket or SSL library (`requests`, `urllib`, `http`, `httpx`, `aiohttp`, `socket`, `ssl`), and no import of a model SDK or framework (`openai`, `anthropic`, `google`, `boto3`, `langchain`, `transformers`).
- **Credentials.** No production or evaluation code reads environment variables (`os.environ`, `getenv`) or refers to an API key. The only use of `os.environ` is in a test (`code/tests/test_steps.py`), which runs `code/main.py` with a `PATH` that lacks Tesseract. The run needs no `.env` file.
- **Processes.** The program starts only one external process, the local `tesseract` binary, which OCRs the 11 PNG screenshots (`code/ghostaudit/evidence/documents.py`). The only other `subprocess` call is in the evaluation script `code/evaluation/main.py`, which runs `code/main.py` on the sample requests; it is not part of the submission run.
- **Dependencies.** `requirements.txt` lists only `pypdf` and `Pillow`, which read the PDFs and prepare the images locally.

## Compute used

Zero tokens does not mean zero compute. The run uses local CPU, memory and disk. Measured on a 4-vCPU Linux container with Python 3.13.17 and Tesseract 5.3.4:

| Run | Wall time | CPU time (user + sys) | Peak memory (largest process) |
| --- | --- | --- | --- |
| First run, empty OCR cache (11 screenshots OCR'd) | 16.1 s | 36.0 s | 84 MB |
| Repeat run, OCR cache filled | 2.2 s | 2.2 s | 81 MB |

- CPU time exceeds wall time on the first run because Tesseract uses several cores.
- The OCR text is cached in `.cache/ocr/` (11 files). Both runs produced the same `output.csv` (MD5 `589503d30c50542082ab4305902dd68b`).
- Timings vary with the machine; the figures are from one measurement each.

## Development assistance

The solution was written with the help of an interactive AI coding agent. That usage happened while developing the code, not when the program runs, so it is not part of the figures above. The conversation is recorded in `log.txt` at the repository root, which is submitted as the chat transcript.
