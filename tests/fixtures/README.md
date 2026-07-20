# RMServer test data

`rmserver/` contains local, potentially sensitive crystal images and is intentionally ignored by Git.

The fixture preserves the directory names observed on RMServer. Both plate directory
forms below are real input forms and must remain supported; they must not be renamed
or normalized inside the fixture.

```text
rmserver/<shard>/plateID_<plate>/batchID_<batch>/wellNum_<number>/profileID_<profile>/d<drop>_*_ef.jpg
rmserver/<shard>/plateID<plate>/batchID_<batch>/wellNum_<number>/profileID_<profile>/d<drop>_*_ef.jpg
```

Observed local examples include `plateID1070`, `plateID_1069`, `plateID_1100`,
`plateID_2069`, and `plateID_2070`. These are distinct plate codes; in particular,
`1070` and `2070` must not be treated as aliases.

Repository unit tests create synthetic directory trees. Tests marked `requires_rmserver_fixture` additionally validate the real local fixture when it is present.
