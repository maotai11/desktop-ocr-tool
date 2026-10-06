"""Verify publisher-pinned model locks; never bless arbitrary replacement bytes.

Model upgrades must deliberately review the upstream source, SHA256, metadata,
license and paired benchmark. This command does not rewrite either manifest.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ocr.model_validator import MODEL_PROFILES, verified_model_manifest


def main():
    for profile in MODEL_PROFILES:
        for role, info in verified_model_manifest(ROOT, profile=profile).items():
            print(f'{profile}/{role}: {info["sha256"]} ({info["size_bytes"]:,} bytes)')
    print('All pinned model assets verified; manifests unchanged.')


if __name__ == '__main__':
    main()
