from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_persist_dir(configured_dir: str) -> Path:
    persist_dir = Path(configured_dir)
    if not persist_dir.is_absolute():
        persist_dir = PROJECT_ROOT / persist_dir
    return persist_dir.resolve()


def resolve_embedding_model_path(model_name: str) -> tuple[str, bool]:
    configured_path = Path(model_name)
    if configured_path.exists():
        return str(configured_path), True

    hub_root = Path.home() / ".cache" / "huggingface" / "hub"
    repo_dir = hub_root / f"models--{model_name.replace('/', '--')}"
    ref_file = repo_dir / "refs" / "main"
    if ref_file.exists():
        snapshot_id = ref_file.read_text(encoding="utf-8").strip()
        snapshot_dir = repo_dir / "snapshots" / snapshot_id
        if snapshot_dir.exists():
            return str(snapshot_dir), True

    return model_name, False
