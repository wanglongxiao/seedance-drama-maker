[KEY ACTION REFERENCE IMAGE HARD-GATE REVIEW]
Inspect the candidate image itself. This is a pass/fail quality gate before the image can enter the asset library.

[AUTHORITATIVE VISIBLE CAST]
- Exact visible human-like subject count: $expected_cast_count
- The only authorized visible named characters are: $expected_cast_names
- Each authorized character must appear exactly once as one continuous body.
- Any person, spirit, apparition, human-like body, duplicate, clone, reflection, portrait, screen image, shadow-double, background pedestrian, crowd member, or partially visible extra body counts as a visible subject.
- Narrative mentions outside the authoritative cast are off-screen context only and must not appear.

[CHARACTER IDENTITY AND WARDROBE REQUIREMENTS]
$character_requirements

[SCENE CONTEXT]
- Scene: $scene_name
- Description: $scene_description
- Character performance context: $character_description
- Camera: $camera_angle

[HARD FAIL CONDITIONS]
Fail the candidate when any one condition is true:
1. The total visible human-like subject count differs from $expected_cast_count.
2. Any authorized character is missing, appears more than once, is replaced by another character, or two roles share the same identity.
3. Any unauthorized foreground or background person appears.
4. Clothing or nudity differs materially from the required current state. Exposed breasts, buttocks, or genitals are unexpected nudity unless the current wardrobe requirement explicitly requires that exposure.
5. A body has extra, missing, fused, detached, shared, floating, or backward limbs; impossible joints; an extra head or torso; or ambiguous limb ownership.
6. The image combines multiple timeline moments, a montage, before/after positions, or repeated bodies.
7. The action, location, or physical staging materially contradicts the scene.

Do not approve based on the generation prompt's intent. Approve only what is visibly present in the candidate. Reference images define identity, embodiment, and wardrobe for their labeled character; they are not extra required cast members.

[OUTPUT]
Return only one JSON object with every field present:
{
  "approved": true,
  "visible_subject_count": 0,
  "character_instances": {
    "exact character name": 0
  },
  "unexpected_subjects": [],
  "identities_distinct": true,
  "wardrobe_consistent": true,
  "unexpected_nudity": false,
  "anatomy_valid": true,
  "single_static_instant": true,
  "scene_semantics_consistent": true,
  "hard_failures": [],
  "feedback": "Concise, concrete visual evidence and the exact correction needed."
}

Use the exact authoritative character names as character_instances keys. Even if uncertain, provide your best integer count and fail the relevant boolean instead of omitting a field.
