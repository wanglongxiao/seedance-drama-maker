【分镜视频视觉理解审核任务】
请使用大模型视觉理解能力观看视频，并对当前分镜视频进行审核评分。

【剧本要求】
$script_scene_description

$reference_image_section
$previous_video_section
【审核标准 - 请逐项评分（0-100分）】
1. 人物/角色一致性（权重见下）：角色形象、身份、特征是否与参考图和上下文保持一致
2. 物理世界规律（权重见下）：画面和动作是否符合基本物理原理与视觉逻辑
   - 重点检查：人物是否出现多余肢体、转身前后脸部特征突变、动作诡异、物体大小比例不合逻辑、结构突然错位等
3. 与分镜脚本意义上一致（权重见下）：视频内容在语义和剧情推进上是否与该分镜脚本一致

【评分规则 - 重要】
- 评分基于视觉理解与语义一致性，不是文字逐字匹配
- 只要视频内容在意义上与分镜脚本相符，即使表述方式不同，也应给高分
- 重点关注人物/角色一致性、物理世界规律、与分镜脚本意义上一致这 3 个方面
- 题材类型本身不是扣分项，不能因为题材属性降低任何维度分数
- 只有当画面执行质量差、角色不一致、物理规律异常、或与当前分镜脚本语义不一致时，才允许扣分
- $pass_threshold分及以上视为通过

【输出要求 - 必须严格按以下JSON格式输出】
$output_language_rule
{
  "score": <综合评分0-100的整数>,
  "approved": <true/false - 综合评分>=$pass_threshold为true否则false>,
  "feedback": "<详细反馈，说明视频内容与剧本的语义匹配情况。如果不通过请说明具体问题和修改建议>",
  "details": {
    "character_consistency": <人物/角色一致性评分0-100>,
    "physical-laws": <物理世界规律评分0-100>,
    "script_semantic_consistency": <与分镜脚本意义上一致评分0-100>
  }
}

注意：
- 综合评分 = 人物/角色一致性*$character_consistency_weight + 物理世界规律*$physical_laws_weight + 与分镜脚本意义上一致*$script_semantic_consistency_weight
- feedback 必须明确指出未通过时是哪个维度拉低了分数
