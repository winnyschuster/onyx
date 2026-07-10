from enum import Enum

from pydantic import BaseModel
from pydantic import Field

from onyx.access.models import ExternalAccess
from onyx.connectors.models import ConnectorCheckpoint


class BoxTraversalMode(str, Enum):
    # Full BFS over the folder tree. Used for the first index, for pruning /
    # permission-sync slim retrieval, for folder-scoped connectors, and as the
    # reconciliation / fallback path.
    FULL = "full"
    # Incremental via the Box enterprise events stream. Used for steady-state
    # short-window polls of a whole-enterprise connector.
    EVENTS = "events"


class BoxAccessContext(BaseModel):
    """Read access accumulated along a Box folder chain (collaborations,
    shared links, and owners). Box collaborations are set on folders and
    cascade to all descendants, so this context is passed down the traversal."""

    user_emails: set[str] = Field(default_factory=set)
    group_ids: set[str] = Field(default_factory=set)
    is_public: bool = False

    def merged_with(self, other: "BoxAccessContext") -> "BoxAccessContext":
        return BoxAccessContext(
            user_emails=self.user_emails | other.user_emails,
            group_ids=self.group_ids | other.group_ids,
            is_public=self.is_public or other.is_public,
        )

    def to_external_access(self) -> ExternalAccess:
        return ExternalAccess(
            external_user_emails=self.user_emails,
            external_user_group_ids=self.group_ids,
            is_public=self.is_public,
        )


class BoxFolderFrontierEntry(BaseModel):
    folder_id: str
    display_name: str
    parent_folder_id: str | None = None
    path: str
    # While queued: access inherited from ancestor folders. Expanded with the
    # folder's own collaborations/shared link when the folder starts processing.
    # None outside permission-sync runs.
    access: BoxAccessContext | None = None


class BoxConnectorCheckpoint(ConnectorCheckpoint):
    # Chosen on the first cycle. None until then.
    mode: BoxTraversalMode | None = None

    # --- FULL (BFS) state ---
    # BFS frontier of folders left to process (empty until seeded; `mode` marks
    # whether the traversal has been decided/seeded yet).
    todo: list[BoxFolderFrontierEntry] = Field(default_factory=list)
    # Folder currently being paginated, with its opaque Box page marker.
    current: BoxFolderFrontierEntry | None = None
    current_marker: str | None = None
    # Folder IDs already processed, so overlapping/duplicate entry roots (e.g.
    # a folder and one of its ancestors both configured) don't double-index.
    # Also used in EVENTS mode to yield each changed file's parent folder node
    # at most once.
    seen_folder_ids: set[str] = Field(default_factory=set)

    # --- EVENTS (incremental) state ---
    # Pagination cursor within the events window (Box's next_stream_position).
    event_stream_position: str | None = None
    # File IDs already handled this run, to dedup a file that appears in
    # multiple events within the window.
    event_seen_file_ids: set[str] = Field(default_factory=set)
