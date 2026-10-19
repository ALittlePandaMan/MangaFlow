"""Explicit installation; models stay local and never receive cloud requests."""
import argparse

from app.services.ocr.local_catalog import LOCAL_OCR_MODELS, resolve_assets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=LOCAL_OCR_MODELS, required=True)
    args = parser.parse_args()
    directory, auxiliary = resolve_assets(args.provider, download=True)
    print(f"Verified {args.provider}: {directory}; auxiliary={auxiliary}", flush=True)


if __name__ == "__main__":
    main()
