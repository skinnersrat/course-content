"""Process tutorials for Neuromatch Academy

- Filter input file list for .ipynb files
- Check that the cells have been executed sequentially on a fresh kernel
- Execute the notebook and fail if errors are encountered
- Extract solution code and write a .py file witht the solution
- Replace solution cells with a "hint" image and a link to the solution code
- Redirect Colab-inserted badges
- Write the executed version of the input notebook to its original path
- Write the post-processed notebook to a student/ subdirectory
- Write solution images to a static/ subdirectory
- Write solution code to a solutions/ subdirectory

"""
import os
import re
import sys
import argparse
import hashlib
from binascii import a2b_base64
from copy import deepcopy
import nbformat
from nbconvert.preprocessors import ExecutePreprocessor


GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/NeuromatchAcademy/course-content/master"
)
GITHUB_TREE_URL = (
    "https://github.com/NeuromatchAcademy/course-content/tree/master/"
)


def main(arglist):
    """Process IPython notebooks from a list of files."""
    args = parse_args(arglist)

    # Filter to only ipython notebook fikes
    nb_paths = [
        arg for arg in args.files
        if arg.endswith(".ipynb") and "student/" not in arg
    ]
    if not nb_paths:
        print("No notebook files found")
        sys.exit(0)

    # Allow environment to override stored kernel name
    exec_kws = {"timeout": 600}
    if "NB_KERNEL" in os.environ:
        exec_kws["kernel_name"] = os.environ["NB_KERNEL"]

    # Defer failures until after processing all notebooks
    errors = {}
    notebooks = {}

    for nb_path in nb_paths:

        # Load the notebook structure
        with open(nb_path) as f:
            nb = nbformat.read(f, nbformat.NO_CONVERT)

        if not sequentially_executed(nb):
            if args.require_sequntial:
                errors[nb_path] = "Notebook is not sequentially executed."
                continue

        # Run the notebook from top to bottom, catching errors
        print(f"Executing {nb_path}")
        executor = ExecutePreprocessor(**exec_kws)
        try:
            executor.preprocess(nb)
        except Exception as err:
            # Log the error, but then continue
            errors[nb_path] = err
        else:
            notebooks[nb_path] = nb

    if errors or args.check_only:
        exit(errors)

    # TODO Check compliancy with PEP8, generate a report, but don't fail

    # TODO Check notebook name format?
    # (If implemented, update the CI workflow to only run on tutorials)

    # Post-process notebooks to remove solution code and write both versions
    for nb_path, nb in notebooks.items():

        # Loop through the cells and fix any Colab badges we encounter
        for cell in nb.get("cells", []):
            if has_colab_badge(cell):
                redirect_colab_badge_to_master_branch(cell)

        # Write out the executed version of the original notebooks
        print(f"Writing complete notebook to {nb_path}")
        with open(nb_path, "w") as f:
            nbformat.write(nb, f)

        # Extract components of the notebook path
        nb_dir, nb_fname = os.path.split(nb_path)
        nb_name, _ = os.path.splitext(nb_fname)

        # Create subdirectories, if they don't exist
        student_dir = make_sub_dir(nb_dir, "student")
        static_dir = make_sub_dir(nb_dir, "static")
        solutions_dir = make_sub_dir(nb_dir, "solutions")

        # Generate the student version and save it to a subdirectory
        print(f"Extracting solutions from {nb_path}")
        processed = extract_solutions(nb, nb_dir, nb_name)
        student_nb, solution_resources, solution_snippets = processed

        # Loop through cells and point the colab badge at the student version
        for cell in student_nb.get("cells", []):
            if has_colab_badge(cell):
                redirect_colab_badge_to_student_version(cell)

        # Write the student version of the notebook
        student_nb_path = os.path.join(student_dir, nb_fname)
        print(f"Writing student notebook to {student_nb_path}")
        with open(student_nb_path, "w") as f:
            nbformat.write(student_nb, f)

        # Write the images extracted from the solution cells
        print(f"Writing solution images to {static_dir}")
        for fname, imdata in solution_resources.items():
            fname = fname.replace("static", static_dir)
            with open(fname, "wb") as f:
                f.write(imdata)

        # Write the solution snippets
        print(f"Writing solution snippets to {solutions_dir}")
        for fname, snippet in solution_snippets.items():
            fname = fname.replace("solutions", solutions_dir)
            with open(fname, "w") as f:
                f.write(snippet)

    exit(errors)


def extract_solutions(nb, nb_dir, nb_name):
    """Convert solution cells to markdown; embed images from Python output."""
    nb = deepcopy(nb)
    _, tutorial_dir = os.path.split(nb_dir)

    solution_resources = {}
    solution_snippets = {}

    nb_cells = nb.get("cells", [])
    for i, cell in enumerate(nb_cells):

        if has_solution(cell):

            # Get the cell source
            cell_source = cell["source"]

            # Hash the source to get a unique identifier
            cell_id = hashlib.sha1(cell_source.encode("utf-8")).hexdigest()[:8]

            # Extract image data from the cell outputs
            cell_images = []
            for j, output in enumerate(cell.get("outputs", [])):

                fname = f"static/{nb_name}_Solution_{cell_id}_{j}.png"
                try:
                    image_data = a2b_base64(output["data"]["image/png"])
                except KeyError:
                    continue
                solution_resources[fname] = image_data
                cell_images.append(fname)

            # Clean up the cell source and assign a filename
            snippet = "\n".join(cell_source.split("\n")[1:])
            py_fname = f"solutions/{nb_name}_Solution_{cell_id}.py"
            solution_snippets[py_fname] = snippet

            # Convert the solution cell to markdown,
            # embed the image as a link to static resource,
            # And insert a link to the solution
            new_source = ""
            if cell_images:
                new_source += "**Example output:**\n\n"
            for f in cell_images:
                url = f"{GITHUB_RAW_URL}/tutorials/{tutorial_dir}/{f}"
                new_source += f"![Solution hint]({url})\n\n"

            url = f"{GITHUB_TREE_URL}/tutorials/{tutorial_dir}/{py_fname}"
            new_source += f"[Click for solution]({url})"

            cell["source"] = new_source
            cell["cell_type"] = "markdown"
            cell["metadata"]["colab_type"] = "text"
            if "outputID" in cell["metadata"]:
                del cell["metadata"]["outputId"]
            if "outputs" in cell:
                del cell["outputs"]
            if "execution_count" in cell:
                del cell["execution_count"]

    return nb, solution_resources, solution_snippets


def has_solution(cell):
    """Return True if cell is marked as containing an exercise solution."""
    cell_text = cell["source"].replace(" ", "").lower()
    first_line = cell_text.split("\n")[0]
    return (
        cell_text.startswith("#@titlesolution")
        or "to_remove" in first_line
    )


def has_colab_badge(cell):
    """Return True if cell has a Colab badge as an HTML element."""
    return "colab-badge.svg" in cell["source"]


def redirect_colab_badge_to_master_branch(cell):
    """Modify the Colab badge to point at the master branch on Github."""
    cell_text = cell["source"]
    p = re.compile(r"^(.+/NeuromatchAcademy/course-content/blob/)\w+(/.+$)")
    cell["source"] = p.sub(r"\1master\2", cell_text)


def redirect_colab_badge_to_student_version(cell):
    """Modify the Colab badge to point at student version of the notebook."""
    cell_text = cell["source"]
    p = re.compile(r"(^.+/tutorials/W\dD\d-\w+)/(\w+\.ipynb.+)")
    cell["source"] = p.sub(r"\1/student/\2", cell_text)


def sequentially_executed(nb):
    """Return True if notebook appears freshly executed from top-to-bottom."""
    exec_counts = [
        cell["execution_count"]
        for cell in nb.get("cells", [])
        if (
            cell["source"]
            and cell.get("execution_count", None) is not None
        )
    ]
    sequential_counts = list(range(1, 1 + len(exec_counts)))
    # Returns True if there are no executed code cells, which is fine?
    return exec_counts == sequential_counts


def make_sub_dir(nb_dir, name):
    """Create nb_dir/name if it does not exist."""
    sub_dir = os.path.join(nb_dir, name)
    if not os.path.exists(sub_dir):
        os.mkdir(sub_dir)
    return sub_dir


def exit(errors):
    """Exit with message and status dependent on contents of errors dict."""
    for failed_file, error in errors.items():
        print(f"{failed_file} failed quality control.")
        print(error)

    status = bool(errors)
    report = "Failure" if status else "Success"
    print("=" * 30, report, "=" * 30)
    sys.exit(status)


def parse_args(arglist):
    """Handle the command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Process neuromatch tutorial notebooks",
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="File name(s) to process. Will filter for .ipynb extension."
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        dest="check_only",
        help="Only run QC checks; don't do post-processing"
    )
    parser.add_argument(
        "--allow-non-sequential",
        action="store_false",
        dest="require_sequntial",
        help="Don't fail if the notebook is not sequentially executed"
    )
    return parser.parse_args(arglist)


if __name__ == "__main__":

    main(sys.argv[1:])
