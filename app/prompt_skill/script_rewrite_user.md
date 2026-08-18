请基于【上一版完整剧本】和【本次修改要求】重新输出一份完整、可直接执行的 JSON 剧本。
【硬性要求】
1. 必须输出完整剧本 JSON，而不是局部修改说明。
2. 必须保留用户没有要求修改的合理内容，按用户要求修改需要调整的部分。
3. scenes 必须是完整数组，scene_number 连续递增。
4. 总时长应尽量维持在约 $total_duration 秒，每个分镜时长仍需满足 $scene_duration_min-$scene_duration_max 秒。
5. dialogue、description、character_description、voice_description、time_of_day、weather 要完整，不要省略。
6. scene_definitions 中每个布景必须保留或补齐 time_of_day、weather、scene_features。
7. 只输出 JSON，不要输出解释、前言、Markdown 代码块。

【上一版完整剧本】
$previous_script_json

【本次修改要求】
$edit_request
