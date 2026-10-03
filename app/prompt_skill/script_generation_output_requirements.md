【输出要求】
$style_output_rule
【强制】角色设定最多 $max_characters 个，布景设定最多 $max_setting_definitions 个，分镜最多 $max_storyboard_scenes 个。
【强制】所有分镜的出场角色必须包含在角色设定中，且分镜出场角色总数最多 $max_characters 个。
【强制】所有分镜实际使用的布景都必须包含在布景设定中，且分镜实际使用布景总数最多 $max_setting_definitions 个。
【强制】scene_definitions 中每个布景必须输出 time_of_day、weather、scene_features；scenes 中每个分镜必须输出 time_of_day、weather，并按顺序在 description 前输出 character_outfits 和 scene_state。
【强制】每个分镜的 description 必须使用从 0 秒开始、连续无重叠且恰好覆盖到 duration 的秒段时间轴；每段都要包含人物动作/表情/眼神/情绪、空间位置与互动、镜头语言、光影变化和明确的剧情推进。
【强制】禁止男性生殖器官特写、口交镜头及性交插入部位/器官/解剖细节特写；成人亲密剧情必须改用非器官焦点的中远景、侧背面、人物表情动作、轮廓和光影表达。
【强制】不同分镜必须保持视觉风格、色调、光源逻辑和镜头语言相对统一；连续分镜必须保持视线、运动方向、空间轴线、人物站位和道具位置可衔接。
【强制】相邻分镜必须因果承接，但不能重复上一分镜已经完成的动作、对白、画面状态或情绪结果。
$output_language_rule
请直接输出JSON格式的剧本内容，不要包含其他说明文字。
