"""F11: the self-hosted pip-cache switch has to actually switch.

`${{ vars.CI_RUNNER && '' || 'pip' }}` evaluates to `pip` whether the variable
is set or not, because GitHub treats the empty string in the middle as FALSE
and falls through to the right-hand side every time. The comments said the
cache upload was disabled on the self-hosted runner; the expression never did
it, and the CI log showed `cache: pip` with a tar warning from the save step.

These tests evaluate the expressions as they are written in the workflow, under
GitHub's documented truthiness rules, for both an unset and a configured
runner variable.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k ci_cache_expression
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import FFmWiz

WORKFLOW = Path(FFmWiz.__file__).resolve().parent / ".github" / "workflows" / "python-smoke.yml"


def github_truthy(value) -> bool:
    """GitHub's rule: '', 0, false and null are false; everything else is true."""
    return not (value in ("", 0, False, None))


def evaluate(expression: str, variables: dict[str, str]):
    """Evaluate one `${{ ... }}` body for the given `vars` context.

    Real `&&`/`||` semantics, not a ternary: `a && b` yields b when a is truthy
    and a otherwise, `a || b` yields a when a is truthy and b otherwise, and
    `&&` binds tighter. That is precisely what the defect turned on -- with
    `A && '' || 'pip'`, a truthy A produces the empty string, which is FALSE,
    so the `||` falls through to 'pip' every single time.
    """
    def literal(token: str):
        token = token.strip()
        if token.startswith("!"):
            return not github_truthy(literal(token[1:]))
        if token.startswith("'") and token.endswith("'"):
            return token[1:-1]
        if token.startswith("vars."):
            return variables.get(token[len("vars."):], "")
        raise AssertionError(f"unsupported token in a cache expression: {token!r}")

    def conjunction(text: str):
        value = None
        for index, token in enumerate(text.split("&&")):
            operand = literal(token)
            if index == 0:
                value = operand
            elif github_truthy(value):
                value = operand
            else:
                break
        return value

    result = None
    for index, part in enumerate(expression.split("||")):
        operand = conjunction(part)
        if index == 0:
            result = operand
        elif not github_truthy(result):
            result = operand
        else:
            break
    return result


def cache_expressions() -> list[str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    found = re.findall(r"cache:\s*\$\{\{(.+?)\}\}", text)
    assert found, "the workflow no longer configures the pip cache through an expression"
    return [expression.strip() for expression in found]


class TheEvaluatorMatchesGithubTruthiness(unittest.TestCase):
    """The rule the bug turned on, stated once so the tests below mean something."""

    def test_an_empty_string_is_false(self):
        self.assertFalse(github_truthy(""))

    def test_a_nonempty_string_is_true(self):
        self.assertTrue(github_truthy("self-hosted"))

    def test_the_old_broken_expression_always_chose_pip(self):
        broken = "vars.CI_RUNNER && '' || 'pip'"
        self.assertEqual(evaluate(broken, {}), "pip")
        self.assertEqual(evaluate(broken, {"CI_RUNNER": "self-hosted"}), "pip",
                         "this is the defect: a set variable still selected pip")


class ThePipCacheFollowsTheRunner(unittest.TestCase):
    def test_the_workflow_still_configures_the_cache_in_both_jobs(self):
        self.assertEqual(len(cache_expressions()), 2)

    def test_a_hosted_runner_keeps_the_pip_cache(self):
        for expression in cache_expressions():
            with self.subTest(expression=expression):
                self.assertEqual(evaluate(expression, {}), "pip")

    def test_a_configured_self_hosted_runner_disables_it(self):
        for expression in cache_expressions():
            for value in ("self-hosted", "[self-hosted, windows]", "my-runner"):
                with self.subTest(expression=expression, value=value):
                    self.assertEqual(evaluate(expression, {"CI_RUNNER": value}), "",
                                     "the expensive cache upload is still enabled")

    def test_an_empty_variable_counts_as_hosted(self):
        for expression in cache_expressions():
            with self.subTest(expression=expression):
                self.assertEqual(evaluate(expression, {"CI_RUNNER": ""}), "pip")

    def test_both_jobs_use_the_same_expression(self):
        self.assertEqual(len(set(cache_expressions())), 1,
                         "the two jobs disagree about when the cache is on")


if __name__ == "__main__":
    unittest.main()
