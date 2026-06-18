from __future__ import annotations
from enum import Enum
from typing import Literal
from pydantic import BaseModel, computed_field


class FeatureStatus(str, Enum):
    gap        = "Gap"
    idea       = "Idea"
    scoped     = "Scoped"
    scored     = "Scored"
    planned    = "Planned"    # backward-compat alias for In-Progress
    in_progress = "In-Progress"
    live       = "Live"
    released   = "Released"


class Feature(BaseModel):
    wbs: str
    name: str
    status: FeatureStatus
    value: int | None = None
    effort: int | None = None
    notes: str = ""

    @computed_field
    @property
    def priority_score(self) -> float | None:
        if self.value is None or self.effort is None or self.effort == 0:
            return None
        return self.value / self.effort


class WBSSubArea(BaseModel):
    wbs_prefix: str
    title: str
    features: list[Feature] = []

    @computed_field
    @property
    def completion_pct(self) -> float:
        if not self.features:
            return 0.0
        return sum(1 for f in self.features if f.status in (FeatureStatus.live, FeatureStatus.released)) / len(self.features)

    @computed_field
    @property
    def live_count(self) -> int:
        return sum(1 for f in self.features if f.status == FeatureStatus.live)

    @computed_field
    @property
    def planned_count(self) -> int:
        return sum(1 for f in self.features if f.status in (FeatureStatus.planned, FeatureStatus.in_progress))

    @computed_field
    @property
    def gap_count(self) -> int:
        return sum(1 for f in self.features if f.status in (
            FeatureStatus.gap, FeatureStatus.idea, FeatureStatus.scoped, FeatureStatus.scored
        ))


class WBSArea(BaseModel):
    number: int
    title: str
    sub_areas: list[WBSSubArea] = []

    @computed_field
    @property
    def all_features(self) -> list[Feature]:
        return [f for sa in self.sub_areas for f in sa.features]

    @computed_field
    @property
    def completion_pct(self) -> float:
        features = self.all_features
        if not features:
            return 0.0
        return sum(1 for f in features if f.status in (FeatureStatus.live, FeatureStatus.released)) / len(features)

    @computed_field
    @property
    def live_count(self) -> int:
        return sum(1 for f in self.all_features if f.status == FeatureStatus.live)

    @computed_field
    @property
    def planned_count(self) -> int:
        return sum(1 for f in self.all_features if f.status in (FeatureStatus.planned, FeatureStatus.in_progress))

    @computed_field
    @property
    def gap_count(self) -> int:
        return sum(1 for f in self.all_features if f.status in (
            FeatureStatus.gap, FeatureStatus.idea, FeatureStatus.scoped, FeatureStatus.scored
        ))


class ScopeItem(BaseModel):
    text: str
    complete: bool = False


class ScopeGroup(BaseModel):
    title: str
    items: list[ScopeItem] = []


class UserPersona(BaseModel):
    name: str
    description: str


class ProductDoc(BaseModel):
    raw_text: str
    title: str = ""
    summary: str = ""
    users: list[UserPersona] = []
    users_md: str = ""          # raw markdown for ## Users section
    scope_groups: list[ScopeGroup] = []
    scope_md: str = ""          # raw markdown for ## Product Scope section
    workflows_md: str = ""      # raw markdown for ## Core Workflows section
    wbs_areas: list[WBSArea] = []
    gaps_md: str = ""

    @computed_field
    @property
    def all_features(self) -> list[Feature]:
        return [f for area in self.wbs_areas for f in area.all_features]

    @computed_field
    @property
    def total_features(self) -> int:
        return len(self.all_features)

    @computed_field
    @property
    def live_count(self) -> int:
        return sum(1 for f in self.all_features if f.status == FeatureStatus.live)

    @computed_field
    @property
    def planned_count(self) -> int:
        return sum(1 for f in self.all_features if f.status in (FeatureStatus.planned, FeatureStatus.in_progress))

    @computed_field
    @property
    def gap_count(self) -> int:
        return sum(1 for f in self.all_features if f.status in (
            FeatureStatus.gap, FeatureStatus.idea, FeatureStatus.scoped, FeatureStatus.scored
        ))

    @computed_field
    @property
    def overall_completion_pct(self) -> float:
        if self.total_features == 0:
            return 0.0
        return sum(1 for f in self.all_features if f.status in (FeatureStatus.live, FeatureStatus.released)) / self.total_features


class ChangelogGroup(BaseModel):
    label: str
    items: list[str] = []


class ChangelogEntry(BaseModel):
    version: str
    in_progress: bool = False
    groups: list[ChangelogGroup] = []
    bug_fixes: list[str] = []


class VersionBucket(BaseModel):
    label: str
    items: list[str] = []


class RoadmapSection(BaseModel):
    name: str
    items: list[str] = []
    buckets: list[VersionBucket] = []   # version sub-buckets (Planned section only)


class AboutDoc(BaseModel):
    raw_text: str
    changelog: list[ChangelogEntry] = []
    roadmap: list[RoadmapSection] = []

    def roadmap_section(self, name: str) -> RoadmapSection | None:
        for s in self.roadmap:
            if s.name == name:
                return s
        return None


# ── Bug tracking ──────────────────────────────────────────────────────────────

class BugSeverity(str, Enum):
    critical = "Critical"
    high     = "High"
    medium   = "Medium"
    low      = "Low"


class BugStatus(str, Enum):
    open        = "Open"
    investigating = "Investigating"
    fix_in_progress = "Fix In Progress"
    resolved    = "Resolved"


class BugItem(BaseModel):
    id: int
    title: str
    severity: BugSeverity = BugSeverity.medium
    status: BugStatus = BugStatus.open
    notes: str = ""
    wbs_ref: str | None = None


class ResolvedBug(BaseModel):
    id: int
    title: str
    resolved_in: str = ""
    date: str = ""


class BugDoc(BaseModel):
    raw_text: str
    active: list[BugItem] = []
    resolved: list[ResolvedBug] = []


class BugCreate(BaseModel):
    title: str
    severity: BugSeverity = BugSeverity.medium
    notes: str = ""
    wbs_ref: str | None = None


class BugUpdate(BaseModel):
    title: str | None = None
    severity: BugSeverity | None = None
    status: BugStatus | None = None
    notes: str | None = None


# API request/response models

class FeatureStatusUpdate(BaseModel):
    status: FeatureStatus


class FeatureUpdate(BaseModel):
    name: str | None = None
    status: FeatureStatus | None = None
    value: int | None = None
    effort: int | None = None
    notes: str | None = None


class NewFeature(BaseModel):
    wbs_prefix: str
    name: str
    status: FeatureStatus = FeatureStatus.idea
    value: int | None = None
    effort: int | None = None
    notes: str = ""


class RoadmapUpdate(BaseModel):
    in_progress: list[str]
    planned: list[str]          # unassigned planned sub-areas
    backlog: list[str]
    planned_buckets: list[VersionBucket] = []  # version-bucketed planned sub-areas


class VersionBucketUpdate(BaseModel):
    label: str
    wbs: list[str] = []


class RoadmapFeaturesUpdate(BaseModel):
    """Feature-level roadmap save: WBS codes per column + freeform items."""
    in_progress_wbs: list[str] = []
    planned_wbs: list[str] = []           # unassigned planned features
    planned_buckets: list[VersionBucketUpdate] = []  # version-bucketed planned features
    backlog_wbs: list[str] = []
    freeform_backlog: list[str] = []


class NewRelease(BaseModel):
    version: str
    bug_fixes: list[str] = []
