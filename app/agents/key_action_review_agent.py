# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

"""Hard-gate visual review for generated key-action reference images."""

import json
import re
import time
from typing import Any, Dict, List, Tuple

import requests

from app.config import config
from app.models.schemas import GeneratedImage, Script
from app.prompt_skill import render_prompt
from app.services.llm_service import llm_service
from app.utils.logger import get_logger

logger = get_logger("key_action_review_agent")


class KeyActionReviewAgent:
    """Reject malformed key-action images before they enter the asset library."""

    def __init__(self):
        self.model = config.get("models.video_review.endpoint")
        self.temperature = float(config.get("key_action_review.temperature", 0.1))
        self.max_tokens = int(config.get("key_action_review.max_tokens", 4000))
        self.request_retry_count = max(
            0,
            int(config.get("key_action_review.request_retry_count", 2)),
        )
        self.request_retry_delay_seconds = max(
            0.0,
            float(config.get("key_action_review.request_retry_delay_seconds", 2)),
        )

    @staticmethod
    def _normalize_name(value: Any) -> str:
        normalized = re.sub(r"^\[|\]$", "", str(value or "").strip()).lower()
        return re.sub(r"[^0-9a-z\u4e00-\u9fff_-]+", "", normalized)

    def _unique_scene_character_names(self, scene) -> List[str]:
        names: List[str] = []
        seen = set()
        for raw_name in getattr(scene, "characters_present", None) or []:
            name = re.sub(r"^\[|\]$", "", str(raw_name or "").strip())
            key = self._normalize_name(name)
            if not name or not key or key in seen:
                continue
            seen.add(key)
            names.append(name)
        return names

    def _build_character_requirements(
        self,
        scene,
        script: Script,
        character_names: List[str],
    ) -> str:
        character_map = {
            self._normalize_name(getattr(character, "name", "")): character
            for character in getattr(script, "characters", None) or []
        }
        outfit_map = {
            self._normalize_name(name): str(outfit or "").strip()
            for name, outfit in (getattr(scene, "character_outfits", None) or {}).items()
        }
        lines: List[str] = []
        for name in character_names:
            character = character_map.get(self._normalize_name(name))
            if character is None:
                lines.append(f"- [{name}]: exactly one visible instance")
                continue
            outfit = outfit_map.get(
                self._normalize_name(name),
                str(getattr(character, "clothing", "") or "").strip(),
            )
            details = [
                f"name=[{name}]",
                f"age={getattr(character, 'age', '')}",
                f"gender={getattr(character, 'gender', '')}",
                f"face={getattr(character, 'face_features', '')}",
                f"hair={getattr(character, 'hairstyle', '')}",
                f"body={getattr(character, 'body_features', '')}",
                f"skin={getattr(character, 'skin_tone', '')}",
                f"required wardrobe/nudity state={outfit or 'use the written character definition'}",
                f"identity={getattr(character, 'identity_background', '')}",
            ]
            lines.append("- " + "; ".join(details))
        return "\n".join(lines) or "- No visible human-like subject is authorized."

    @staticmethod
    def _reference_label(image: GeneratedImage) -> str:
        reference_type = str(getattr(image, "reference_type", "") or "reference")
        return f"{getattr(image, 'name', '') or 'unnamed'} ({reference_type})"

    def _build_messages(
        self,
        scene,
        script: Script,
        candidate_url: str,
        reference_images: List[GeneratedImage],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        character_names = self._unique_scene_character_names(scene)
        prompt = render_prompt(
            "key_action_image_review.md",
            expected_cast_count=len(character_names),
            expected_cast_names=", ".join(f"[{name}]" for name in character_names) or "(none)",
            character_requirements=self._build_character_requirements(
                scene,
                script,
                character_names,
            ),
            scene_description=str(getattr(scene, "description", "") or ""),
            character_description=str(getattr(scene, "character_description", "") or ""),
            scene_name=str(getattr(scene, "scene_name", "") or ""),
            camera_angle=str(getattr(scene, "camera_angle", "") or ""),
        )
        content: List[Dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {
                "type": "text",
                "text": "CANDIDATE IMAGE TO REVIEW (do not treat this as a reference image):",
            },
            {"type": "image_url", "image_url": {"url": candidate_url}},
        ]
        for index, image in enumerate(reference_images, start=1):
            content.extend([
                {
                    "type": "text",
                    "text": f"REFERENCE IMAGE {index}: {self._reference_label(image)}",
                },
                {"type": "image_url", "image_url": {"url": image.url}},
            ])
        return [{"role": "user", "content": content}], character_names

    @staticmethod
    def _parse_json_output(output: Any) -> Dict[str, Any]:
        text = str(output or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise
            parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise ValueError("review result must be a JSON object")
        return parsed

    @staticmethod
    def _strict_bool(value: Any) -> bool:
        return value is True

    @staticmethod
    def _strict_int(value: Any) -> int:
        if isinstance(value, bool):
            return -1
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    def _enforce_hard_gates(
        self,
        result: Dict[str, Any],
        character_names: List[str],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        expected_count = len(character_names)
        violations: List[str] = []

        visible_count = self._strict_int(result.get("visible_subject_count"))
        if visible_count != expected_count:
            violations.append(
                f"visible subject count is {visible_count}, expected exactly {expected_count}"
            )

        raw_instances = result.get("character_instances")
        instance_map: Dict[str, int] = {}
        if isinstance(raw_instances, dict):
            instance_map = {
                self._normalize_name(name): self._strict_int(count)
                for name, count in raw_instances.items()
            }
        for name in character_names:
            count = instance_map.get(self._normalize_name(name), -1)
            if count != 1:
                violations.append(f"[{name}] appears {count} time(s), expected exactly once")

        unexpected_subjects = result.get("unexpected_subjects")
        if not isinstance(unexpected_subjects, list):
            violations.append("unexpected_subjects was not returned as a list")
        elif unexpected_subjects:
            violations.append(
                "unexpected visible subject(s): "
                + ", ".join(str(item) for item in unexpected_subjects)
            )

        boolean_gates = {
            "identities_distinct": "expected character identities are duplicated, merged, or swapped",
            "wardrobe_consistent": "wardrobe or nudity state does not match the scene",
            "anatomy_valid": "body topology or anatomy is malformed",
            "single_static_instant": "the image combines multiple moments or repeated bodies",
            "scene_semantics_consistent": "the image materially contradicts the scene",
        }
        for key, message in boolean_gates.items():
            if not self._strict_bool(result.get(key)):
                violations.append(message)

        if result.get("unexpected_nudity") is not False:
            violations.append("unexpected nudity is present or was not conclusively ruled out")

        model_failures = result.get("hard_failures")
        if isinstance(model_failures, list):
            for failure in model_failures:
                text = str(failure or "").strip()
                if text and text not in violations:
                    violations.append(text)

        approved = not violations
        feedback = str(result.get("feedback") or "").strip()
        if violations:
            machine_feedback = "; ".join(violations)
            feedback = f"{feedback}; {machine_feedback}".strip("; ")
        result["approved_by_hard_gates"] = approved
        result["enforced_violations"] = violations
        return approved, feedback, result

    def review_image(
        self,
        scene,
        script: Script,
        candidate_url: str,
        reference_images: List[GeneratedImage] | None = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Review one candidate and fail closed on malformed or unavailable results."""
        reference_images = list(reference_images or [])
        messages, character_names = self._build_messages(
            scene,
            script,
            candidate_url,
            reference_images,
        )
        result = None
        try:
            for attempt in range(self.request_retry_count + 1):
                try:
                    logger.info(
                        "[KEY_ACTION_REVIEW] Reviewing scene %s candidate (attempt %s/%s)",
                        getattr(scene, "scene_number", ""),
                        attempt + 1,
                        self.request_retry_count + 1,
                    )
                    result = llm_service.chat_completion(
                        model=self.model,
                        messages=messages,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                    break
                except requests.exceptions.HTTPError as exc:
                    status_code = exc.response.status_code if exc.response is not None else None
                    retryable = (
                        attempt < self.request_retry_count
                        and status_code in {400, 408, 429, 500, 502, 503, 504}
                    )
                    if not retryable:
                        raise
                    time.sleep(self.request_retry_delay_seconds)

            if not result or not result.get("choices"):
                return False, "Key-action visual review returned no result.", {
                    "enforced_violations": ["review returned no result"],
                }
            output = result["choices"][0]["message"]["content"]
            parsed = self._parse_json_output(output)
            return self._enforce_hard_gates(parsed, character_names)
        except Exception as exc:
            logger.error(
                "[KEY_ACTION_REVIEW] Scene %s review failed: %s",
                getattr(scene, "scene_number", ""),
                str(exc),
            )
            return False, f"Key-action visual review failed: {exc}", {
                "enforced_violations": ["review request or response parsing failed"],
            }
