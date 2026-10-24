"""A member project can itself be a program: its own PROGRAM.MD lists more projects."""
import pytest

from app import scope
from app.manifest import ManifestError, ProjectSpec, parse_manifest, repo_identity
from app.registry import Registry, run_git
from tests.test_project_scope import _git, make_origin, remote_text  # noqa: F401  (fixtures used below)

HEADER = "## Projects\n\n| Key | Repo / Path | Depends On | Kind |\n| --- | --- | --- | --- |\n"


def make_program_repo(tmp_path, name, rows, subdir=""):
    """A bare repo whose PROGRAM.MD lists `rows` [(key, repo, deps, kind)]."""
    bare = tmp_path / f"{name}.git"
    bare.mkdir()
    _git(bare, "init", "-q", "--bare", "-b", "main")
    seed = tmp_path / f"seed-{name}"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    folder = seed / subdir if subdir else seed
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "PROGRAM.MD").write_text(HEADER + "".join(f"| {k} | {r} | {d} | {kind} |\n" for k, r, d, kind in rows))
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "program")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-q", "origin", "main")
    return bare


def push_program(tmp_path, bare, rows):
    work = tmp_path / f"edit-{bare.stem}-{len(list(tmp_path.glob('edit-*')))}"
    run_git(None, ["clone", "-q", str(bare), str(work)])
    (work / "PROGRAM.MD").write_text(HEADER + "".join(f"| {k} | {r} | {d} | {kind} |\n" for k, r, d, kind in rows))
    _git(work, "commit", "-qam", "change")
    _git(work, "push", "-q", "origin", "main")


@pytest.fixture()
def nested(tmp_path):
    a = make_origin(tmp_path, "a", "A")
    b = make_origin(tmp_path, "b", "B")
    inner = make_program_repo(tmp_path, "inner", [("a", a, "", "project"), ("b", b, "a", "project")])
    top = make_origin(tmp_path, "top", "Top")
    specs = [ProjectSpec(key="top", repo=str(top)),
             ProjectSpec(key="inner", repo=str(inner), kind="program", depends_on=()),
             ProjectSpec(key="late", repo=str(make_origin(tmp_path, "late", "Late")), depends_on=("inner.b",))]
    reg = Registry(specs, tmp_path / "cache")
    scope.set_registry(reg)
    yield reg, {"a": a, "b": b, "inner": inner, "top": top}
    scope.set_registry(None)


def test_kind_column_and_dependencies_on_nested_projects_parse(tmp_path):
    specs = parse_manifest(HEADER + f"| inner | /srv/inner.git | | program |\n| x | /srv/x.git | inner.b | |\n", tmp_path)
    assert [s.kind for s in specs] == ["program", "project"] and specs[1].depends_on == ("inner.b",)
    with pytest.raises(ManifestError, match="unknown kind"):
        parse_manifest(HEADER + "| x | /srv/x.git | | weird |\n", tmp_path)
    with pytest.raises(ManifestError, match="unknown project"):
        parse_manifest(HEADER + "| x | /srv/x.git | nope.b | |\n", tmp_path)       # `nope` is not a nested program


def test_expand_lists_the_nested_projects_prefixed_and_hides_the_container(nested):
    reg, _ = nested
    assert reg.keys() == ["top", "late"]                                 # before expansion: only the declared projects
    assert reg.expand() == {}
    assert reg.keys() == ["top", "inner.a", "inner.b", "late"]
    assert "inner" not in reg and "inner.a" in reg
    assert reg.get("inner.b").spec.depends_on == ("inner.a",) and reg.get("inner.b").spec.parent == "inner"
    assert reg.get("late").spec.depends_on == ("inner.b",)


def test_a_nested_project_is_readable_and_editable_like_any_other(nested, client):
    reg, origins = nested
    reg.expand()
    assert "ALogin" in client.get("/p/inner.a/features").text
    assert client.patch("/p/inner.a/api/features/1.2.3", json={"owner": "Ada"}).status_code == 200
    assert "Ada" in run_git(origins["a"], ["show", "main:PRODUCT.MD"])
    assert client.get("/p/inner/dashboard").status_code == 404          # the container itself is not a project
    overview = client.get("/p").text
    assert "inner.a" in overview and "in inner" in overview and "could not be expanded" not in overview


def test_a_change_to_the_nested_manifest_shows_on_the_next_expand(nested, tmp_path):
    reg, origins = nested
    reg.expand()
    c = make_origin(tmp_path, "c", "C")
    push_program(tmp_path, origins["inner"], [("a", origins["a"], "", "project"), ("b", origins["b"], "a", "project"),
                                              ("c", c, "b", "project")])
    reg.expand()
    assert "inner.c" in reg.keys() and reg.get("inner.c").spec.depends_on == ("inner.b",)
    push_program(tmp_path, origins["inner"], [("a", origins["a"], "", "project")])
    errors = reg.expand()                      # `late` still depends on the removed inner.b: the whole list is refused
    assert "inner.b" in errors["*"] and reg.keys() == ["top", "late"]
    push_program(tmp_path, origins["inner"], [("a", origins["a"], "", "project"), ("b", origins["b"], "a", "project")])
    assert reg.expand() == {} and reg.keys() == ["top", "inner.a", "inner.b", "late"]       # and recovers when fixed


def test_a_program_that_lists_itself_is_refused_by_repository(tmp_path):
    a = make_origin(tmp_path, "a", "A")
    inner = make_program_repo(tmp_path, "inner", [("a", a, "", "project")])
    # inner's manifest lists inner's own repository as a nested program
    push_program(tmp_path, inner, [("a", a, "", "project"), ("again", inner, "", "program")])
    reg = Registry([ProjectSpec(key="inner", repo=str(inner), kind="program")], tmp_path / "cache")
    errors = reg.expand()
    assert "inner.again" in errors and "lists itself" in errors["inner.again"]
    assert reg.keys() == ["inner.a"]                                      # the rest still loads


def test_a_cycle_through_two_programs_is_refused(tmp_path):
    x = make_program_repo(tmp_path, "x", [])
    y = make_program_repo(tmp_path, "y", [("x", x, "", "program")])
    push_program(tmp_path, x, [("y", y, "", "program")])
    reg = Registry([ProjectSpec(key="x", repo=str(x), kind="program")], tmp_path / "cache")
    errors = reg.expand()
    assert any("lists itself" in why for why in errors.values())


def test_the_depth_limit_is_a_setting(tmp_path):
    leaf = make_origin(tmp_path, "leaf", "Leaf")
    l3 = make_program_repo(tmp_path, "l3", [("leaf", leaf, "", "project")])
    l2 = make_program_repo(tmp_path, "l2", [("l3", l3, "", "program")])
    l1 = make_program_repo(tmp_path, "l1", [("l2", l2, "", "program")])
    spec = ProjectSpec(key="l1", repo=str(l1), kind="program")
    shallow = Registry([spec], tmp_path / "c1", nested_depth=2)
    errors = shallow.expand()
    assert "l1.l2.l3" in errors and "nested more than 2" in errors["l1.l2.l3"] and shallow.keys() == []
    deep = Registry([spec], tmp_path / "c2", nested_depth=3)
    assert deep.expand() == {} and deep.keys() == ["l1.l2.l3.leaf"]
    off = Registry([spec], tmp_path / "c3", nested_depth=0)
    assert "l1" in off.expand() and off.keys() == []


def test_an_unreachable_nested_program_does_not_take_the_rest_down(nested, tmp_path):
    reg, origins = nested
    reg._declared.append(ProjectSpec(key="gone", repo="/nonexistent/x.git", kind="program"))
    errors = reg.expand()
    assert "gone" in errors and "inner.a" in reg.keys() and "top" in reg.keys()


def test_repo_identity_ignores_case_dot_git_and_trailing_slash():
    assert repo_identity("https://github.com/Acme/Plan.git") == repo_identity("git@github.com:acme/plan") == \
        repo_identity("https://github.com/acme/plan/")
    assert repo_identity("https://github.com/acme/plan", "a") != repo_identity("https://github.com/acme/plan", "b")
