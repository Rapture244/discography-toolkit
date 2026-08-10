# tests/cli/commands/tags/test_genre.py
"""Tests for the `rapt tags genre` command.

These exercise the wiring rather than the logic: whether the command
finds the right files, respects a dry run, refuses bad input, and stops
when the user says no. What to write is the operation's job and is tested
there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from discography_toolkit.cli.main import app
from discography_toolkit.core import metadata
from discography_toolkit.core.metadata import Tag

import pytest
from tests.helpers import silence

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

runner = CliRunner()


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def shelf(tmp_path: Path) -> Path:
    """Build a genre shelf holding two artists and one loose track.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        The shelf's path.
    """
    root: Path = tmp_path / "Jazz"
    for artist, albums in (
        ("USA/Miles Davis - [2 • 2F • 0L • 0M]", ("01. (1959) - Kind of Blue [FLAC]",)),
        ("Japan/Casiopea - [1 • 1F • 0L • 0M]", ("01. (1979) - Casiopea [FLAC]",)),
    ):
        for album in albums:
            folder: Path = root / artist / "FLAC" / album
            folder.mkdir(parents=True)
            for index in (1, 2):
                silence(folder / f"0{index}.flac")

    (root / "Unsorted").mkdir(parents=True)
    silence(root / "Unsorted" / "loose.flac")

    return root


def genres_under(root: Path) -> set[str]:
    """Collect the Genre tag of every track beneath a folder.

    Args:
        root: The folder to scan.

    Returns:
        The distinct genres found.
    """
    return {metadata.read(track, [Tag.GENRE])[Tag.GENRE] for track in root.rglob("*.flac")}


# ==================================================================================== #
#                                       TAGGING                                        #
# ==================================================================================== #
def test_tags_every_track_beneath_the_path(shelf: Path) -> None:
    """The path is a scope: everything under it is tagged.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="y\n")

    assert result.exit_code == 0
    assert genres_under(shelf) == {"Jazz"}


def test_tags_a_loose_track_outside_any_artist(shelf: Path) -> None:
    """A file under no recognized artist is still in scope.

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="y\n")

    loose: Path = shelf / "Unsorted" / "loose.flac"
    assert metadata.read(loose, [Tag.GENRE])[Tag.GENRE] == "Jazz"


def test_lists_the_artists_it_found(shelf: Path) -> None:
    """The banner names the artists beneath the target.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="n\n")

    assert "Miles Davis - [2 • 2F • 0L • 0M]" in result.output
    assert "Casiopea - [1 • 1F • 0L • 0M]" in result.output


def test_an_artist_is_not_listed_beneath_itself(shelf: Path) -> None:
    """Pointing at one artist lists no children: they are albums.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [2 • 2F • 0L • 0M]"

    result = runner.invoke(app, ["tags", "genre", "-p", str(artist), "-g", "Jazz"], input="n\n")

    # Children are printed indented under the banner; the name also
    # appears as the banner target and the progress label, so the indent
    # is what distinguishes a listing from those.
    assert f"  {artist.name}" not in result.output.splitlines()


def test_a_second_run_reports_nothing_to_do(shelf: Path) -> None:
    """Tagging twice is tagging once.

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="y\n")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"])

    assert "Nothing to do" in result.output


def test_the_value_is_written_verbatim(shelf: Path) -> None:
    """A compound genre stays one string; nothing splits it.

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(
        app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz;Jazz Fusion"], input="y\n"
    )

    assert genres_under(shelf) == {"Jazz;Jazz Fusion"}


# ==================================================================================== #
#                                     NOT WRITING                                      #
# ==================================================================================== #
def test_dry_run_changes_nothing(shelf: Path) -> None:
    """A dry run reports the change and leaves the files alone.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz", "--dry-run"])

    assert "Dry run" in result.output
    assert genres_under(shelf) == {""}


def test_declining_the_prompt_changes_nothing(shelf: Path) -> None:
    """Answering no stops before any write.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="n\n")

    assert "Aborted" in result.output
    assert genres_under(shelf) == {""}


def test_an_empty_genre_is_refused(shelf: Path) -> None:
    """Whitespace is not a genre, and must not clear the tag by accident.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "   "])

    # Asserting the message, not just the code: an unanswered confirmation
    # prompt also exits 1, so the code alone would pass either way.
    assert "Genre cannot be empty" in result.output
    assert genres_under(shelf) == {""}


def test_a_folder_without_audio_exits_cleanly(tmp_path: Path) -> None:
    """Nothing to do is not an error.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    result = runner.invoke(app, ["tags", "genre", "-p", str(tmp_path), "-g", "Jazz"])

    assert result.exit_code == 0
    assert "No audio files" in result.output


def test_prompts_for_both_when_given_neither(shelf: Path) -> None:
    """Bare `rapt genre` asks for the path, then the genre.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "genre"], input=f'"{shelf}"\nJazz\ny\n')

    assert result.exit_code == 0
    assert genres_under(shelf) == {"Jazz"}


# ==================================================================================== #
#                                     DECLARATIONS                                     #
# ==================================================================================== #
def declare(folder: Path, value: str) -> None:
    """Write a `.genre` declaration into a folder.

    Args:
        folder: The folder to declare for.
        value: What it should declare.
    """
    _ = (folder / ".genre").write_text(f"{value}\n", encoding="utf-8", newline="\n")


def test_the_nearest_declaration_wins(shelf: Path) -> None:
    """An album's own file beats its artist's, which beats the shelf's.

    The whole precedence rule in one assertion: three declarations at
    three depths, and each track takes the closest one above it.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    album: Path = artist / "FLAC" / "01. (1959) - Kind of Blue [FLAC]"
    declare(shelf, "Shelf")
    declare(artist, "Artist")
    declare(album, "Album")

    # Decline the rename offer, then confirm the tagging.
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="n\ny\n")

    assert genres_under(album) == {"Album"}
    assert genres_under(shelf / "Japan") == {"Shelf"}
    assert genres_under(shelf / "Unsorted") == {"Shelf"}


def test_a_declaration_covering_everything_is_never_asked_past(shelf: Path) -> None:
    """With every track declared, no genre is wanted and none is asked for.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "Jazz")

    # No genre on the line and none on stdin beyond the two confirms: a
    # prompt for a value here would abort.
    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="n\ny\n")

    assert result.exit_code == 0
    assert genres_under(shelf) == {"Jazz"}


def test_a_declaration_beats_the_supplied_genre(shelf: Path) -> None:
    """`--genre` is the fallback, not the answer.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    declare(artist, "Bebop")

    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="y\n")

    assert genres_under(artist) == {"Bebop"}
    assert genres_under(shelf / "Japan") == {"Jazz"}


def test_the_run_leaves_its_declaration_at_the_path(shelf: Path) -> None:
    """Asked once, never again: the answer is written where it was scoped.

    Read as bytes, not text: `read_text` translates CRLF back to LF, so a
    file written with the platform's line ending would pass this on every
    platform while holding different bytes on each. `.editorconfig` asks
    for LF, and only bytes can say whether it got one.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"

    _ = runner.invoke(app, ["tags", "genre", "-p", str(artist), "-g", "Bebop"], input="y\n")

    assert (artist / ".genre").read_bytes() == b"Bebop\n"
    assert not (shelf / ".genre").exists()


def test_a_declaration_is_written_even_when_the_tags_are_already_right(shelf: Path) -> None:
    """Correct tags with no declaration still owe one, or the next run asks again.

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="y\n")
    (shelf / ".genre").unlink()

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="y\n")

    assert result.exit_code == 0
    assert (shelf / ".genre").read_bytes() == b"Jazz\n"


def test_a_declared_shelf_is_a_no_op_on_the_second_run(shelf: Path) -> None:
    """The declaration is what makes the run idempotent.

    The rename offer still has to be declined: a settled shelf is asked
    whether its answer still holds before it is told there is nothing to
    do.

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="y\n")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="n\n")

    assert "Nothing to do" in result.output


def test_a_declaration_above_the_path_is_out_of_scope(shelf: Path) -> None:
    """The search stops at the path given: scope is scope.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    declare(shelf, "Shelf")

    _ = runner.invoke(app, ["tags", "genre", "-p", str(artist), "-g", "Bebop"], input="y\n")

    assert genres_under(artist) == {"Bebop"}


def test_a_declaration_names_its_source(shelf: Path) -> None:
    """A hidden file deciding the genre must say so, relative to the target.

    Absolute would be a hundred characters of which seven matter, which
    is a line nobody reads.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "Jazz")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="y\n")

    assert ".genre" in result.output
    assert str(shelf) not in result.output.split("Genre ->")[1]


def test_the_declaration_is_written_verbatim(shelf: Path) -> None:
    """A compound genre survives the round trip through the file.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "Jazz;Jazz Fusion")

    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="n\ny\n")

    assert genres_under(shelf) == {"Jazz;Jazz Fusion"}


@pytest.mark.parametrize(
    ("contents", "complaint"),
    [("   \n", "is empty"), ("Jazz\nRock\n", "more than one line")],
)
def test_an_unusable_declaration_is_refused(shelf: Path, contents: str, complaint: str) -> None:
    """Neither nothing nor two things is a genre, and neither is guessed at.

    Args:
        shelf: The fixture shelf.
        contents: What the declaration holds.
        complaint: The phrase the refusal should carry.
    """
    _ = (shelf / ".genre").write_text(contents, encoding="utf-8")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"])

    assert result.exit_code == 1
    assert complaint in result.output
    assert genres_under(shelf) == {""}


def test_dry_run_writes_no_declaration(shelf: Path) -> None:
    """A dry run leaves the shelf exactly as it found it.

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz", "--dry-run"])

    assert not (shelf / ".genre").exists()


def test_declining_writes_no_declaration(shelf: Path) -> None:
    """Answering no stops before the declaration as well as the tags.

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="n\n")

    assert not (shelf / ".genre").exists()


# ==================================================================================== #
#                                        FORCING                                       #
# ==================================================================================== #
def test_force_clears_every_declaration_beneath_the_path(shelf: Path) -> None:
    """Forcing leaves one declaration where there were three.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    album: Path = artist / "FLAC" / "01. (1959) - Kind of Blue [FLAC]"
    declare(artist, "Bebop")
    declare(album, "Cool")

    _ = runner.invoke(
        app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz", "--force"], input="y\n"
    )

    assert list(shelf.rglob(".genre")) == [shelf / ".genre"]
    assert genres_under(shelf) == {"Jazz"}


def test_force_asks_when_given_no_genre(shelf: Path) -> None:
    """`--force` alone prompts: it always needs a value, declared or not.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "Jazz")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "--force"], input="Koto\ny\n")

    assert result.exit_code == 0
    assert genres_under(shelf) == {"Koto"}
    assert (shelf / ".genre").read_bytes() == b"Koto\n"


def test_force_shows_what_each_declaration_holds_before_asking(shelf: Path) -> None:
    """Nobody should be asked to overwrite something they cannot see.

    The value is what makes the prompt answerable: the new genre is often
    the old one extended, which needs the old one in front of you.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    declare(artist, "Mande;(GIN) Djembe")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "--force"], input="Jazz\nn\n")

    assert "Mande;(GIN) Djembe" in result.output
    # Named relative to the target, and before the prompt rather than after.
    assert result.output.index("Mande;(GIN) Djembe") < result.output.index("Enter the genre")


def test_force_names_an_unusable_declaration_rather_than_refusing(shelf: Path) -> None:
    """A file on its way out must not stop the run that is removing it.

    Args:
        shelf: The fixture shelf.
    """
    _ = (shelf / ".genre").write_text("   \n", encoding="utf-8", newline="\n")

    result = runner.invoke(
        app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz", "--force"], input="y\n"
    )

    assert result.exit_code == 0
    assert "(unusable)" in result.output
    assert (shelf / ".genre").read_bytes() == b"Jazz\n"


def test_force_says_what_it_would_delete(shelf: Path) -> None:
    """The one destructive step here is named before it is taken.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]", "Bebop")

    result = runner.invoke(
        app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz", "--force"], input="n\n"
    )

    assert "1 '.genre' file(s) beneath this path will be deleted" in result.output
    assert (shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]" / ".genre").exists()


def test_a_declared_path_is_offered_a_rename(shelf: Path) -> None:
    """With every track answered, the useful question is whether the answer still holds.

    Tagged first, because a rename swaps a value the files already carry
    -- it does not tag what was never tagged.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "(JP) Koto")
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="n\ny\n")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="y\n(JPN) Koto\ny\n")

    assert "Replace it?" in result.output
    assert (shelf / ".genre").read_bytes() == b"(JPN) Koto\n"
    assert genres_under(shelf) == {"(JPN) Koto"}


def test_declining_the_rename_offer_tags_as_usual(shelf: Path) -> None:
    """Saying no falls through to the run that was always going to happen.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "(JP) Koto")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="n\ny\n")

    assert result.exit_code == 0
    assert (shelf / ".genre").read_bytes() == b"(JP) Koto\n"
    assert genres_under(shelf) == {"(JP) Koto"}


def test_a_rename_does_not_tag_a_file_that_never_carried_the_genre(shelf: Path) -> None:
    """A rename swaps a value; it does not put one where there was none.

    The declaration is corrected either way, so the next ordinary run
    writes it to the files.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "(JP) Koto")

    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="y\n(JPN) Koto\ny\n")

    assert (shelf / ".genre").read_bytes() == b"(JPN) Koto\n"
    assert genres_under(shelf) == {""}


def test_the_rename_offer_shows_the_nested_declarations_first(shelf: Path) -> None:
    """The matches below are on screen before the question about them.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    declare(shelf, "(JP) Koto")
    declare(artist, "(JP) Koto;Classical")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="n\nn\n")

    assert "(JP) Koto;Classical" in result.output
    assert result.output.index("(JP) Koto;Classical") < result.output.index("Replace it?")


def test_a_supplied_genre_is_not_offered_a_rename(shelf: Path) -> None:
    """An answered run has nothing to ask; the offer is for the bare one.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "(JP) Koto")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="y\n")

    assert "Replace it?" not in result.output


def test_an_undeclared_path_is_not_offered_a_rename(shelf: Path) -> None:
    """Nothing to rename when the path declares nothing of its own.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="Jazz\ny\n")

    assert "Replace it?" not in result.output
    assert genres_under(shelf) == {"Jazz"}


def test_the_confirm_names_the_genre_when_a_run_has_only_one(shelf: Path) -> None:
    """The question has to connect to the declaration three lines above it.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "Soul - Southern")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="n\nn\n")

    assert "do not yet carry 'Soul - Southern'" in result.output


def test_the_confirm_describes_the_work_when_a_run_has_several(shelf: Path) -> None:
    """With several declarations, no single value is the run's to name.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "Soul - Southern")
    declare(shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]", "Soul - Northern")

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="n\nn\n")

    assert "do not match what their folder declares" in result.output


def test_the_intent_does_not_name_a_supplied_genre_twice(shelf: Path) -> None:
    """The "will be declared" sentence beside it already says which value.

    Asserted against the two lines rather than the whole output, and with
    a genre that is not the shelf's name: the fixture shelf is called
    "Jazz", so a genre of "Jazz" would match the folder name in the same
    sentence and prove nothing.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Bebop"], input="n\n")
    lines: list[str] = result.output.splitlines()
    written: str = next(line for line in lines if "will have their Genre written" in line)
    declared: str = next(line for line in lines if "will be declared" in line)

    assert "Bebop" not in written
    assert "'Bebop'" in declared


def test_the_confirmation_names_the_work_and_the_target(shelf: Path) -> None:
    """The question carries the run, so a scrolled shelf still answers it.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Bebop"], input="n\n")

    assert f"Write Genre to 5 file(s) beneath {shelf.name!r}?" in result.output


def test_the_confirmation_names_the_declaration_when_no_tag_is_owed(shelf: Path) -> None:
    """Tags already right and the declaration gone: the question is the file.

    Naming "0 file(s)" would describe work that is not happening, in the
    one run whose only remaining act is writing the `.genre` back.

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Bebop"], input="y\n")
    (shelf / ".genre").unlink()

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Bebop"], input="n\n")

    assert f"Declare 'Bebop' in {shelf.name!r}?" in result.output


# ==================================================================================== #
#                                       RENAMING                                       #
# ==================================================================================== #
def test_rename_changes_the_tags_and_the_declarations_together(shelf: Path) -> None:
    """One correction, both stores: typing it twice is how they drift apart.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "(JP) Shakuhachi")
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf)], input="n\ny\n")

    result = runner.invoke(
        app,
        [
            "tags",
            "genre",
            "-p",
            str(shelf),
            "--rename",
            "(JP) Shakuhachi",
            "-g",
            "(JPN) Shakuhachi",
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    assert genres_under(shelf) == {"(JPN) Shakuhachi"}
    assert (shelf / ".genre").read_bytes() == b"(JPN) Shakuhachi\n"


def test_rename_reaches_a_declaration_nested_below(shelf: Path) -> None:
    """An album's own file is corrected alongside its artist's.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    album: Path = artist / "FLAC" / "01. (1959) - Kind of Blue [FLAC]"
    declare(artist, "(JP) Shakuhachi")
    declare(album, "(JP) Shakuhachi;Classical")

    _ = runner.invoke(
        app,
        [
            "tags",
            "genre",
            "-p",
            str(shelf),
            "--rename",
            "(JP) Shakuhachi",
            "-g",
            "(JPN) Shakuhachi",
        ],
        input="y\n",
    )

    assert (artist / ".genre").read_bytes() == b"(JPN) Shakuhachi\n"
    assert (album / ".genre").read_bytes() == b"(JPN) Shakuhachi;Classical\n"


def test_rename_leaves_the_other_parts_of_a_compound_value_alone(shelf: Path) -> None:
    """Part-wise, not substring: "Classical" is not collateral damage.

    Args:
        shelf: The fixture shelf.
    """
    album: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    metadata.write(
        album / "FLAC" / "01. (1959) - Kind of Blue [FLAC]" / "01.flac",
        {Tag.GENRE: "(JP) Shakuhachi;Classical"},
    )

    _ = runner.invoke(
        app,
        [
            "tags",
            "genre",
            "-p",
            str(shelf),
            "--rename",
            "(JP) Shakuhachi",
            "-g",
            "(JPN) Shakuhachi",
        ],
        input="y\n",
    )

    assert "(JPN) Shakuhachi;Classical" in genres_under(shelf)


def test_rename_leaves_an_unmatched_value_byte_for_byte(shelf: Path) -> None:
    """A rename must not quietly tidy files it had no business editing.

    Args:
        shelf: The fixture shelf.
    """
    album: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    metadata.write(
        album / "FLAC" / "01. (1959) - Kind of Blue [FLAC]" / "01.flac",
        {Tag.GENRE: "Hip Hop; Soul"},
    )

    result = runner.invoke(
        app, ["tags", "genre", "-p", str(shelf), "--rename", "Jazz", "-g", "Bebop"]
    )

    assert "Nothing beneath this path carries" in result.output
    assert "Hip Hop; Soul" in genres_under(shelf)


def test_rename_does_not_leave_a_duplicate_behind(shelf: Path) -> None:
    """Renaming onto a genre a file already carries leaves one of it.

    Args:
        shelf: The fixture shelf.
    """
    album: Path = shelf / "USA" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    metadata.write(
        album / "FLAC" / "01. (1959) - Kind of Blue [FLAC]" / "01.flac",
        {Tag.GENRE: "(JP) Koto;(JPN) Koto"},
    )

    _ = runner.invoke(
        app,
        ["tags", "genre", "-p", str(shelf), "--rename", "(JP) Koto", "-g", "(JPN) Koto"],
        input="y\n",
    )

    assert "(JPN) Koto" in genres_under(shelf)
    assert "(JPN) Koto;(JPN) Koto" not in genres_under(shelf)


def test_rename_asks_for_the_replacement_when_given_none(shelf: Path) -> None:
    """`--rename` alone still needs to know what to replace it with.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "(JP) Koto")

    result = runner.invoke(
        app,
        ["tags", "genre", "-p", str(shelf), "--rename", "(JP) Koto"],
        input="(JPN) Koto\ny\n",
    )

    assert result.exit_code == 0
    assert (shelf / ".genre").read_bytes() == b"(JPN) Koto\n"


def test_rename_and_force_together_are_refused(shelf: Path) -> None:
    """One edits the declarations, the other deletes them.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(
        app, ["tags", "genre", "-p", str(shelf), "--rename", "Jazz", "-g", "Bebop", "--force"]
    )

    assert result.exit_code == 1
    assert "Pick one" in result.output


def test_rename_writes_nothing_on_a_dry_run(shelf: Path) -> None:
    """Neither store is touched when only asked what would change.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "(JP) Koto")

    _ = runner.invoke(
        app,
        [
            "tags",
            "genre",
            "-p",
            str(shelf),
            "--rename",
            "(JP) Koto",
            "-g",
            "(JPN) Koto",
            "--dry-run",
        ],
    )

    assert (shelf / ".genre").read_bytes() == b"(JP) Koto\n"
    assert genres_under(shelf) == {""}


# ==================================================================================== #
#                                    INTERACTIVITY                                     #
# ==================================================================================== #
def test_reports_a_file_it_cannot_read(shelf: Path, make_broken: Callable[[Path], None]) -> None:
    """An unreadable file is listed, and the rest are still tagged.

    Args:
        shelf: The fixture shelf.
        make_broken: Corrupts a file in place.
    """
    broken: Path = shelf / "Unsorted" / "loose.flac"
    make_broken(broken)

    intact: Path = next((shelf / "USA").rglob("*.flac"))

    result = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Jazz"], input="y\n")

    assert "could not be read" in result.output
    assert metadata.read(intact, [Tag.GENRE])[Tag.GENRE] == "Jazz"


@pytest.fixture()
def make_broken() -> Callable[[Path], None]:
    """Return a helper replacing a file's contents with non-audio.

    Returns:
        A callable corrupting the given path.
    """

    def corrupt(path: Path) -> None:
        _ = path.write_bytes(b"not audio at all")

    return corrupt
