【输出要求】
$style_output_rule
【强制】角色设定最多 $max_characters 个，布景设定最多 $max_setting_definitions 个，分镜最多 $max_storyboard_scenes 个。
【强制】所有分镜的出场角色必须包含在角色设定中，且分镜出场角色总数最多 $max_characters 个。
【强制】所有分镜实际使用的布景都必须包含在布景设定中，且分镜实际使用布景总数最多 $max_setting_definitions 个。
【强制】scene_definitions 中每个布景必须输出 time_of_day、weather、scene_features；scenes 中每个分镜必须输出 time_of_day、weather，并按顺序在 description 前输出 character_outfits 和 scene_state。
【强制】相邻分镜必须因果承接，但不能重复上一分镜已经完成的动作、对白、画面状态或情绪结果。
$output_language_rule
请直接输出JSON格式的剧本内容，不要包含其他说明文字。
