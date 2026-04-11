"""Skill abstraction and registry for the Supervisor agent.

A Skill encapsulates:
  - A prompt extension (appended to the Supervisor system prompt)
  - A list of tool names the skill depends on
  - Optional decision hints for the LLM

Skills are resolved at runtime from a code-only registry -- no DB models.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Skill:
    """Immutable skill definition."""

    name: str
    description: str
    prompt_extension: str  # Appended to system prompt
    tools: List[str] = field(default_factory=list)
    decision_hints: List[str] = field(default_factory=list)


class SkillRegistry:
    """Singleton registry mapping skill names to Skill instances."""

    _skills: Dict[str, Skill] = {}

    @classmethod
    def register(cls, skill: Skill) -> None:
        cls._skills[skill.name] = skill
        logger.debug("Registered skill: %s", skill.name)

    @classmethod
    def get(cls, name: str) -> Optional[Skill]:
        return cls._skills.get(name)

    @classmethod
    def get_all(cls) -> Dict[str, Skill]:
        return dict(cls._skills)

    @classmethod
    def get_by_names(cls, names: List[str]) -> List[Skill]:
        """Return skills in the requested order, skipping unknown names."""
        return [cls._skills[n] for n in names if n in cls._skills]

    @classmethod
    def all_tools(cls, skill_names: Optional[List[str]] = None) -> List[str]:
        """Return merged tool list from the requested skills (or all)."""
        skills = cls._skills.values()
        if skill_names:
            skills = [cls._skills[n] for n in skill_names if n in cls._skills]
        seen = set()
        result: List[str] = []
        for s in skills:
            for t in s.tools:
                if t not in seen:
                    seen.add(t)
                    result.append(t)
        return result

    @classmethod
    def compose_prompt(cls, skill_names: Optional[List[str]] = None) -> str:
        """Concatenate prompt extensions from the requested skills (or all)."""
        skills = cls._skills.values()
        if skill_names:
            skills = [cls._skills[n] for n in skill_names if n in cls._skills]
        parts: List[str] = []
        for s in skills:
            if s.prompt_extension:
                parts.append(s.prompt_extension.strip())
        return "\n\n".join(parts)

    @classmethod
    def compose_hints(cls, skill_names: Optional[List[str]] = None) -> List[str]:
        """Concatenate decision hints from the requested skills (or all)."""
        skills = cls._skills.values()
        if skill_names:
            skills = [cls._skills[n] for n in skill_names if n in cls._skills]
        hints: List[str] = []
        for s in skills:
            hints.extend(s.decision_hints)
        return hints

    @classmethod
    def clear(cls) -> None:
        cls._skills.clear()


def register_skill(skill: Skill) -> Skill:
    """Convenience function to register a skill and return it."""
    SkillRegistry.register(skill)
    return skill
