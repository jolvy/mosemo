"""The metric definition the analytics results were computed with."""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class AnalyticsDefinition:
    """Metric definition version and the sha256 of the bundled PSL snapshot.

    Changing `public_suffix_list.dat` changes site keys, so it needs a new version.
    """

    version: int
    psl_snapshot: str


CURRENT_DEFINITION = AnalyticsDefinition(
    version=1,
    psl_snapshot="db4ccf1f59d72022396cfe7c9ed3e107a927fce3532b4cd6eeb6f8a9dc525f05",
)
