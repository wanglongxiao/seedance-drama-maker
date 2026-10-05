请基于【上一版完整剧本】和【本次修改要求】重新输出一份完整、可直接执行的 JSON 剧本。
【硬性要求】
1. 必须输出完整剧本 JSON，而不是局部修改说明。
2. 必须保留用户没有要求修改的合理内容，按用户要求修改需要调整的部分。
3. scenes 必须是完整数组，scene_number 连续递增。
4. 总时长应尽量维持在约 $total_duration 秒，每个分镜时长仍需满足 $scene_duration_min-$scene_duration_max 秒；必须按动作节点、人物数量、信息量、调度和情绪转折重新判断时长，不同丰富度的分镜不得机械使用相同时长。
5. description 必须补足环境空间、人物位置与动作、细微表演、互动结果、镜头调度、光影变化和剧情推进；时长越长，连续秒段与可执行内容必须越丰富，禁止重复动作凑时长。
6. 相邻分镜转场必须由剧情动作、视线、声音、道具、构图、遮挡或光影变化自然驱动；保留有效承接，移除“下一镜/留下悬念/制造钩子”等元叙事文字及无动机的黑屏、闪白、旋转、粒子、故障、连续甩镜和频繁淡入淡出。
7. dialogue、description、character_description、voice_description、time_of_day、weather 要完整，不要省略。
8. scene_definitions 中每个布景必须保留或补齐 time_of_day、weather、scene_features。
9. 仅当运行时“NSFW 运行策略：已开启”且相关角色明确年满18岁时：若上一版角色已全裸、半裸、局部裸露或只穿内衣，必须把该裸露层级连续继承到后续秒段和连续分镜；镜头切换、遮挡、被单、换场、时间跳跃、性爱结束或默认 clothing 均不得自动恢复衣物。只有 description 完整展示穿衣动作后，后续秒段或下一分镜才可降低裸露层级。
10. 只输出 JSON，不要输出解释、前言、Markdown 代码块。

【上一版完整剧本】
$previous_script_json

【本次修改要求】
$edit_request
