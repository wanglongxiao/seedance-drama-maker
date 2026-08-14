# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

from typing import Dict, Optional


SUPPORTED_OUTPUT_LANGUAGES: Dict[str, str] = {
    "zh-CN": "简体中文",
    "zh-TW": "繁體中文",
    "en": "English",
    "ja": "日本語",
    "es": "Español",
}


BACKEND_TRANSLATIONS = {
    "chat.system_prompt": {
        "zh-CN": "你是一个专业的AI视频生成助手。你的任务是：\n1. 理解用户的视频创作需求\n2. 询问必要的细节（如风格、角色、场景等）\n3. 引导用户提供参考图片或语音描述\n4. 当信息足够时，告知用户可以开始生成视频\n\n请用友好、专业的语气与用户交流。",
        "zh-TW": "你是一個專業的AI影片生成助手。你的任務是：\n1. 理解使用者的影片創作需求\n2. 詢問必要的細節（如風格、角色、場景等）\n3. 引導使用者提供參考圖片或語音描述\n4. 當資訊足夠時，告知使用者可以開始生成影片\n\n請用友善、專業的語氣與使用者交流。",
        "en": "You are a professional AI video creation assistant. Your tasks are:\n1. Understand the user's video creation requirements\n2. Ask for the necessary details such as style, characters, and scenes\n3. Guide the user to provide reference images or voice descriptions\n4. Tell the user when enough information has been collected and video generation can start\n\nRespond in a friendly and professional tone.",
        "ja": "あなたはプロのAI動画生成アシスタントです。あなたの役割は次のとおりです。\n1. ユーザーの動画制作要件を理解する\n2. スタイル、登場人物、シーンなど必要な詳細を確認する\n3. 参考画像や音声説明の提供を案内する\n4. 情報が十分に集まったら、動画生成を開始できることを伝える\n\n丁寧でプロフェッショナルな口調で応答してください。",
        "es": "Eres un asistente profesional de generación de video con IA. Tus tareas son:\n1. Comprender los requisitos creativos del usuario\n2. Pedir los detalles necesarios, como estilo, personajes y escenas\n3. Guiar al usuario para que aporte imágenes de referencia o descripciones de voz\n4. Informar al usuario cuando ya haya suficiente información para comenzar la generación\n\nResponde con un tono amable y profesional.",
    },
    "chat.append_input": {
        "zh-CN": "✅ 收到你的补充需求！\n\n**已记录的信息：**\n{details}\n\n我会将这些需求融入到视频创作中。",
        "zh-TW": "✅ 收到你的補充需求！\n\n**已記錄的資訊：**\n{details}\n\n我會把這些需求融入影片創作中。",
        "en": "✅ Got your additional requirements.\n\n**Recorded Information**\n{details}\n\nI will incorporate these requirements into the video creation process.",
        "ja": "✅ 追加の要望を受け取りました。\n\n**記録済みの内容**\n{details}\n\nこれらの要望を動画制作に反映します。",
        "es": "✅ He recibido tus requisitos adicionales.\n\n**Información registrada**\n{details}\n\nIncorporaré estos requisitos al proceso de creación del video.",
    },
    "chat.error": {
        "zh-CN": "抱歉，处理您的消息时出现了错误: {error}",
        "zh-TW": "抱歉，處理你的訊息時發生錯誤: {error}",
        "en": "Sorry, an error occurred while processing your message: {error}",
        "ja": "申し訳ありません。メッセージの処理中にエラーが発生しました: {error}",
        "es": "Lo siento, se produjo un error al procesar tu mensaje: {error}",
    },
    "progress.script.generating": {
        "zh-CN": "正在生成剧本...",
        "zh-TW": "正在生成劇本...",
        "en": "Generating script...",
        "ja": "脚本を生成しています...",
        "es": "Generando guion...",
    },
    "progress.script.completed": {
        "zh-CN": "剧本生成完成",
        "zh-TW": "劇本生成完成",
        "en": "Script generation completed",
        "ja": "脚本生成が完了しました",
        "es": "La generación del guion se completó",
    },
    "progress.reference.generating": {
        "zh-CN": "正在生成参考图库...",
        "zh-TW": "正在生成參考圖庫...",
        "en": "Generating reference library...",
        "ja": "参照画像ライブラリを生成しています...",
        "es": "Generando la biblioteca de referencias...",
    },
    "progress.reference.completed_wait": {
        "zh-CN": "参考图库生成完成，等待用户确认...",
        "zh-TW": "參考圖庫生成完成，等待使用者確認...",
        "en": "Reference library generated. Waiting for confirmation...",
        "ja": "参照画像ライブラリの生成が完了しました。確認待ちです...",
        "es": "La biblioteca de referencias se generó. Esperando confirmación...",
    },
    "progress.reference.recompleted": {
        "zh-CN": "参考图库重新生成完成",
        "zh-TW": "參考圖庫重新生成完成",
        "en": "Reference library regenerated",
        "ja": "参照画像ライブラリの再生成が完了しました",
        "es": "La biblioteca de referencias se regeneró",
    },
    "progress.reference.regenerating": {
        "zh-CN": "正在重新生成参考图库...",
        "zh-TW": "正在重新生成參考圖庫...",
        "en": "Regenerating reference library...",
        "ja": "参照画像ライブラリを再生成しています...",
        "es": "Regenerando la biblioteca de referencias...",
    },
    "message.reference.confirm_prompt": {
        "zh-CN": "参考图库已生成完成，请确认是否满意。满意后请输入'确认'或点击继续按钮开始生成分镜视频。",
        "zh-TW": "參考圖庫已生成完成，請確認是否滿意。滿意後請輸入「確認」或點擊繼續按鈕開始生成分鏡影片。",
        "en": "The reference library is fully ready. Please confirm it. If it looks good, enter 'confirm' or click Continue to start generating the scene videos.",
        "ja": "参照画像ライブラリの生成が完了しました。内容をご確認ください。問題なければ「confirm」と入力するか、続行ボタンを押してシーン動画の生成を開始してください。",
        "es": "La biblioteca de referencias ya está completa. Confírmala y, si está bien, escribe 'confirm' o pulsa Continuar para empezar a generar los videos por escena.",
    },
    "message.reference.regenerated_confirm_prompt": {
        "zh-CN": "参考图库已重新生成，请确认是否满意。",
        "zh-TW": "參考圖庫已重新生成，請確認是否滿意。",
        "en": "The reference library has been regenerated. Please confirm whether it is satisfactory.",
        "ja": "参照画像ライブラリが再生成されました。内容をご確認ください。",
        "es": "La biblioteca de referencias se regeneró. Confirma si te parece correcta.",
    },
    "message.reference.confirmed_start_videos": {
        "zh-CN": "已确认参考图库，正在开始生成分镜视频...",
        "zh-TW": "已確認參考圖庫，正在開始生成分鏡影片...",
        "en": "Reference library confirmed. Starting storyboard video generation...",
        "ja": "参照画像ライブラリを確認しました。絵コンテ動画の生成を開始しています...",
        "es": "La biblioteca de referencias se confirmó. Iniciando la generación de videos del storyboard...",
    },
    "message.direct_start_script": {
        "zh-CN": "已收到你的需求，正在直接开始生成剧本...",
        "zh-TW": "已收到你的需求，正在直接開始生成劇本...",
        "en": "Your request has been received. Starting script generation directly...",
        "ja": "ご要望を受け取りました。脚本生成を直接開始しています...",
        "es": "Se recibió tu solicitud. Iniciando la generación del guion directamente...",
    },
    "message.script.updated": {
        "zh-CN": "已根据你的修改要求重新生成并替换剧本。",
        "zh-TW": "已根據你的修改要求重新生成並替換劇本。",
        "en": "The script has been regenerated and replaced based on your revision request.",
        "ja": "修正要望に基づいて脚本を再生成し、置き換えました。",
        "es": "El guion se regeneró y reemplazó según tu solicitud de cambios.",
    },
    "message.rollback.success": {
        "zh-CN": "已成功退回到步骤“{step}”",
        "zh-TW": "已成功退回到步驟「{step}」",
        "en": "Successfully rolled back to step \"{step}\"",
        "ja": "ステップ「{step}」まで正常にロールバックしました",
        "es": "Se volvió correctamente al paso \"{step}\"",
    },
    "step.script.complete": {
        "zh-CN": "📖 剧本生成完成！是否继续生成图片？",
        "zh-TW": "📖 劇本生成完成！是否繼續生成圖片？",
        "en": "📖 Script generation is complete. Continue to image generation?",
        "ja": "📖 脚本生成が完了しました。画像生成に進みますか？",
        "es": "📖 La generación del guion terminó. ¿Continuar con la generación de imágenes?",
    },
    "step.reference.complete": {
        "zh-CN": "🎨 参考图库已生成完成！请确认是否满意，满意后继续生成分镜视频。",
        "zh-TW": "🎨 參考圖庫已生成完成！請確認是否滿意，滿意後繼續生成分鏡影片。",
        "en": "🎨 The reference library is complete. Confirm it before continuing to scene video generation.",
        "ja": "🎨 参照画像ライブラリの生成が完了しました。確認後にシーン動画生成へ進んでください。",
        "es": "🎨 La biblioteca de referencias está completa. Confírmala antes de continuar con la generación de videos por escena.",
    },
    "step.videos.complete": {
        "zh-CN": "🎬 视频生成完成！是否合成最终视频？",
        "zh-TW": "🎬 影片生成完成！是否合成最終影片？",
        "en": "🎬 Video generation is complete. Merge the final video now?",
        "ja": "🎬 動画生成が完了しました。最終動画を結合しますか？",
        "es": "🎬 La generación de video terminó. ¿Deseas unir el video final ahora?",
    },
    "message.reference.category1_confirm_prompt": {
        "zh-CN": "人物/角色图库与布景参考图库已生成完成，请确认是否满意。满意后请输入'确认'或点击继续按钮进入下一阶段。",
        "zh-TW": "人物/角色圖庫與布景參考圖庫已生成完成，請確認是否滿意。滿意後請輸入「確認」或點擊繼續按鈕進入下一階段。",
        "en": "The character gallery and backdrop reference gallery are ready. Please confirm. If they look good, enter 'confirm' or click Continue to proceed to the next stage.",
        "ja": "キャラクター画像ライブラリと背景セット参照ライブラリの生成が完了しました。内容をご確認ください。問題なければ「confirm」と入力するか、続行ボタンを押して次の段階へ進んでください。",
        "es": "La galería de personajes y la galería de referencias de escenografía están listas. Confírmalas y, si están bien, escribe 'confirm' o pulsa Continuar para pasar a la siguiente etapa.",
    },
    "message.reference.category2_confirm_prompt": {
        "zh-CN": "角色装扮图与场景状态图已生成完成，请确认是否满意。满意后请输入'确认'或点击继续按钮进入下一阶段。",
        "zh-TW": "角色裝扮圖與場景狀態圖已生成完成，請確認是否滿意。滿意後請輸入「確認」或點擊繼續按鈕進入下一階段。",
        "en": "The character outfit images and scene state images are ready. Please confirm. If they look good, enter 'confirm' or click Continue to proceed to the next stage.",
        "ja": "キャラクター衣装画像とシーン状態画像の生成が完了しました。内容をご確認ください。問題なければ「confirm」と入力するか、続行ボタンを押して次の段階へ進んでください。",
        "es": "Las imágenes de vestuario de los personajes y las imágenes de estado de escena están listas. Confírmalas y, si están bien, escribe 'confirm' o pulsa Continuar para pasar a la siguiente etapa.",
    },
    "message.reference.category3_confirm_prompt": {
        "zh-CN": "各分镜故事版已生成完成，请确认是否满意。满意后请输入'确认'或点击继续按钮开始生成分镜视频。",
        "zh-TW": "各分鏡故事版已生成完成，請確認是否滿意。滿意後請輸入「確認」或點擊繼續按鈕開始生成分鏡影片。",
        "en": "The storyboard images for all scenes are ready. Please confirm. If they look good, enter 'confirm' or click Continue to start generating the scene videos.",
        "ja": "各シーンの絵コンテ画像の生成が完了しました。内容をご確認ください。問題なければ「confirm」と入力するか、続行ボタンを押してシーン動画の生成を開始してください。",
        "es": "Las imágenes del storyboard de todas las escenas están listas. Confírmalas y, si están bien, escribe 'confirm' o pulsa Continuar para empezar a generar los videos por escena.",
    },
    "progress.reference.category1_completed_wait": {
        "zh-CN": "人物/角色图库与布景参考图库生成完成，等待用户确认...",
        "zh-TW": "人物/角色圖庫與布景參考圖庫生成完成，等待使用者確認...",
        "en": "Character gallery and backdrop reference gallery generated. Waiting for confirmation...",
        "ja": "キャラクター画像ライブラリと背景セット参照ライブラリの生成が完了しました。確認待ちです...",
        "es": "Se generaron la galería de personajes y la galería de referencias de escenografía. Esperando confirmación...",
    },
    "progress.reference.category2_completed_wait": {
        "zh-CN": "角色装扮图与场景状态图生成完成，等待用户确认...",
        "zh-TW": "角色裝扮圖與場景狀態圖生成完成，等待使用者確認...",
        "en": "Character outfit images and scene state images generated. Waiting for confirmation...",
        "ja": "キャラクター衣装画像とシーン状態画像の生成が完了しました。確認待ちです...",
        "es": "Se generaron las imágenes de vestuario de los personajes y las imágenes de estado de escena. Esperando confirmación...",
    },
    "progress.reference.category3_completed_wait": {
        "zh-CN": "各分镜故事版生成完成，等待用户确认...",
        "zh-TW": "各分鏡故事版生成完成，等待使用者確認...",
        "en": "Storyboard images generated. Waiting for confirmation...",
        "ja": "各シーンの絵コンテ画像の生成が完了しました。確認待ちです...",
        "es": "Se generaron las imágenes del storyboard. Esperando confirmación...",
    },
    "step.reference.category1_complete": {
        "zh-CN": "🎨 人物/角色图库与布景参考图库已生成完成！请确认后继续。",
        "zh-TW": "🎨 人物/角色圖庫與布景參考圖庫已生成完成！請確認後繼續。",
        "en": "🎨 The character gallery and backdrop reference gallery are complete. Confirm to continue.",
        "ja": "🎨 キャラクター画像ライブラリと背景セット参照ライブラリの生成が完了しました。確認して続行してください。",
        "es": "🎨 La galería de personajes y la galería de referencias de escenografía están completas. Confirma para continuar.",
    },
    "step.reference.category2_complete": {
        "zh-CN": "🎨 角色装扮图与场景状态图已生成完成！请确认后继续。",
        "zh-TW": "🎨 角色裝扮圖與場景狀態圖已生成完成！請確認後繼續。",
        "en": "🎨 The character outfit images and scene state images are complete. Confirm to continue.",
        "ja": "🎨 キャラクター衣装画像とシーン状態画像の生成が完了しました。確認して続行してください。",
        "es": "🎨 Las imágenes de vestuario de los personajes y las imágenes de estado de escena están completas. Confirma para continuar.",
    },
    "step.reference.category3_complete": {
        "zh-CN": "🎨 各分镜故事版已生成完成！请确认后继续生成分镜视频。",
        "zh-TW": "🎨 各分鏡故事版已生成完成！請確認後繼續生成分鏡影片。",
        "en": "🎨 The storyboard images are complete. Confirm before continuing to scene video generation.",
        "ja": "🎨 各シーンの絵コンテ画像の生成が完了しました。確認後にシーン動画生成へ進んでください。",
        "es": "🎨 Las imágenes del storyboard están completas. Confírmalas antes de continuar con la generación de videos por escena.",
    },
    "progress.video.scene_generating": {
        "zh-CN": "分镜 {scene} 生成中...",
        "zh-TW": "分鏡 {scene} 生成中...",
        "en": "Generating scene {scene}...",
        "ja": "シーン{scene}を生成しています...",
        "es": "Generando la escena {scene}...",
    },
    "progress.video.scene_regenerating": {
        "zh-CN": "分镜 {scene} 第{retry}次重新生成中...",
        "zh-TW": "分鏡 {scene} 第{retry}次重新生成中...",
        "en": "Regenerating scene {scene}, retry {retry}...",
        "ja": "シーン{scene}を再生成しています。{retry}回目のリトライ...",
        "es": "Regenerando la escena {scene}, intento {retry}...",
    },
    "message.video.scene_generation_failed_retry": {
        "zh-CN": "分镜 {scene} 生成失败，正在进行第{retry}次重试...",
        "zh-TW": "分鏡 {scene} 生成失敗，正在進行第{retry}次重試...",
        "en": "Scene {scene} generation failed. Starting retry {retry}...",
        "ja": "シーン{scene}の生成に失敗しました。リトライ{retry}回目を開始します...",
        "es": "La generación de la escena {scene} falló. Iniciando el reintento {retry}...",
    },
    "message.video.scene_generation_failed_limit_reason": {
        "zh-CN": "连续生成失败，已达到次数上限 {limit}。最后错误：{error}",
        "zh-TW": "連續生成失敗，已達到次數上限 {limit}。最後錯誤：{error}",
        "en": "Generation kept failing and reached the limit of {limit}. Last error: {error}",
        "ja": "生成失敗が続き、上限 {limit} 回に達しました。最後のエラー: {error}",
        "es": "La generación siguió fallando y alcanzó el límite de {limit}. Último error: {error}",
    },
    "message.video.scene_generated_wait_review": {
        "zh-CN": "分镜 {scene} 已生成，等待审核...",
        "zh-TW": "分鏡 {scene} 已生成，等待審核...",
        "en": "Scene {scene} has been generated and is waiting for review...",
        "ja": "シーン{scene}が生成され、レビュー待ちです...",
        "es": "La escena {scene} ya se generó y está esperando revisión...",
    },
    "message.video.duplicate_seed_retry": {
        "zh-CN": "分镜 {scene} 本次生成返回的 seed={seed} 与本任务已有 seed 重复，跳过审核并自动重新生成。",
        "zh-TW": "分鏡 {scene} 本次生成返回的 seed={seed} 與本任務已有 seed 重複，跳過審核並自動重新生成。",
        "en": "Scene {scene} returned seed={seed}, which duplicates an existing seed in this task. Review is skipped and regeneration starts automatically.",
        "ja": "シーン{scene}で返された seed={seed} は、このタスク内の既存 seed と重複しています。レビューをスキップして自動再生成します。",
        "es": "La escena {scene} devolvió seed={seed}, que ya existe en esta tarea. Se omite la revisión y se regenera automáticamente.",
    },
    "message.video.duplicate_seed_retry_limit": {
        "zh-CN": "分镜 {scene} 连续返回重复 seed，已达到次数上限 {limit}。最后一个重复 seed 为 {seed}。",
        "zh-TW": "分鏡 {scene} 連續返回重複 seed，已達到次數上限 {limit}。最後一個重複 seed 為 {seed}。",
        "en": "Scene {scene} kept returning duplicate seeds and reached the limit of {limit}. The last duplicate seed was {seed}.",
        "ja": "シーン{scene}は重複 seed を返し続け、上限 {limit} 回に達しました。最後の重複 seed は {seed} です。",
        "es": "La escena {scene} siguió devolviendo seeds duplicadas y alcanzó el límite de {limit}. La última seed duplicada fue {seed}.",
    },
    "message.video.scene_skipped_continue": {
        "zh-CN": "分镜 {scene} 已被跳过并从脚本中移除。原因：{reason}。将直接继续生成新的分镜 {next_scene}。",
        "zh-TW": "分鏡 {scene} 已被跳過並從腳本中移除。原因：{reason}。將直接繼續生成新的分鏡 {next_scene}。",
        "en": "Scene {scene} was skipped and removed from the script. Reason: {reason}. Continuing directly with the new scene {next_scene}.",
        "ja": "シーン{scene}はスキップされ、脚本から削除されました。理由: {reason}。新しいシーン{next_scene}の生成を続行します。",
        "es": "La escena {scene} se omitió y se eliminó del guion. Motivo: {reason}. Se continuará directamente con la nueva escena {next_scene}.",
    },
    "message.video.scene_skipped_no_next": {
        "zh-CN": "分镜 {scene} 已被跳过并从脚本中移除。原因：{reason}。当前已没有后续分镜。",
        "zh-TW": "分鏡 {scene} 已被跳過並從腳本中移除。原因：{reason}。目前已沒有後續分鏡。",
        "en": "Scene {scene} was skipped and removed from the script. Reason: {reason}. There are no remaining scenes after it.",
        "ja": "シーン{scene}はスキップされ、脚本から削除されました。理由: {reason}。この後に残っているシーンはありません。",
        "es": "La escena {scene} se omitió y se eliminó del guion. Motivo: {reason}. Ya no quedan escenas después de ella.",
    },
    "message.video.scene_skipped_reason_user": {
        "zh-CN": "用户手动点击了“跳过”",
        "zh-TW": "使用者手動點擊了「跳過」",
        "en": "The user clicked Skip manually",
        "ja": "ユーザーが手動で「スキップ」をクリックしました",
        "es": "El usuario hizo clic manualmente en Omitir",
    },
    "message.video.scene_can_skip": {
        "zh-CN": "分镜 {scene} 连续生成失败，已达到次数上限 {limit}。现在可以点击“跳过”移除该分镜并继续后续流程。",
        "zh-TW": "分鏡 {scene} 連續生成失敗，已達到次數上限 {limit}。現在可以點擊「跳過」移除該分鏡並繼續後續流程。",
        "en": "Scene {scene} kept failing to generate and reached the limit of {limit}. You can now click Skip to remove it and continue the workflow.",
        "ja": "シーン{scene}は生成失敗が続き、上限 {limit} 回に達しました。「スキップ」をクリックすると、このシーンを削除してフローを続行できます。",
        "es": "La escena {scene} siguió fallando al generarse y alcanzó el límite de {limit}. Ahora puedes hacer clic en Omitir para eliminarla y continuar el flujo.",
    },
    "message.video.no_remaining_scenes_ready_merge": {
        "zh-CN": "当前脚本中已没有剩余分镜，可以继续进入合成步骤。",
        "zh-TW": "目前腳本中已沒有剩餘分鏡，可以繼續進入合成步驟。",
        "en": "There are no remaining scenes in the script. You can continue to the merge step.",
        "ja": "脚本に残っているシーンはありません。結合ステップへ進めます。",
        "es": "Ya no quedan escenas en el guion. Puedes continuar al paso de unión.",
    },
    "message.video.scene_reviewing": {
        "zh-CN": "分镜 {scene} 正在审核...",
        "zh-TW": "分鏡 {scene} 正在審核...",
        "en": "Reviewing scene {scene}...",
        "ja": "シーン{scene}をレビューしています...",
        "es": "Revisando la escena {scene}...",
    },
    "progress.video.reviewing": {
        "zh-CN": "正在审核分镜 {scene}/{total} (第{attempt}次)...",
        "zh-TW": "正在審核分鏡 {scene}/{total} (第{attempt}次)...",
        "en": "Reviewing scene {scene}/{total} (attempt {attempt})...",
        "ja": "シーン{scene}/{total}をレビューしています（{attempt}回目）...",
        "es": "Revisando la escena {scene}/{total} (intento {attempt})...",
    },
    "message.video.review_passed": {
        "zh-CN": "分镜 {scene} 审核通过 (评分: {score}分)",
        "zh-TW": "分鏡 {scene} 審核通過 (評分: {score}分)",
        "en": "Scene {scene} passed review (score: {score})",
        "ja": "シーン{scene}はレビューに合格しました（スコア: {score}）",
        "es": "La escena {scene} aprobó la revisión (puntuación: {score})",
    },
    "message.video.review_failed": {
        "zh-CN": "分镜 {scene} 审核未通过 (评分: {score}分): {feedback}",
        "zh-TW": "分鏡 {scene} 審核未通過 (評分: {score}分): {feedback}",
        "en": "Scene {scene} failed review (score: {score}): {feedback}",
        "ja": "シーン{scene}はレビュー不合格です（スコア: {score}）: {feedback}",
        "es": "La escena {scene} no aprobó la revisión (puntuación: {score}): {feedback}",
    },
    "message.video.review_failed_after_max_retry": {
        "zh-CN": "分镜 {scene} 审核未通过且已达到最大重试次数({max_retries}次)，最终评分 {score} 分。失败原因：{feedback}",
        "zh-TW": "分鏡 {scene} 審核未通過且已達到最大重試次數({max_retries}次)，最終評分 {score} 分。失敗原因：{feedback}",
        "en": "Scene {scene} failed review and reached the maximum retry limit ({max_retries}). Final score: {score}. Reason: {feedback}",
        "ja": "シーン{scene}はレビュー不合格のまま最大リトライ回数（{max_retries}回）に達しました。最終スコア: {score}。理由: {feedback}",
        "es": "La escena {scene} no aprobó la revisión y alcanzó el máximo de reintentos ({max_retries}). Puntuación final: {score}. Motivo: {feedback}",
    },
    "message.video.auto_mode_select_best": {
        "zh-CN": "分镜 {scene} 在自动模式下已达到最大重试次数({max_retries}次)，未达到目标分数。系统已自动选择评分最高的视频继续流程，最高分为 {score} 分。参考反馈：{feedback}",
        "zh-TW": "分鏡 {scene} 在自動模式下已達到最大重試次數({max_retries}次)，仍未達到目標分數。系統已自動選擇評分最高的影片繼續流程，最高分為 {score} 分。參考回饋：{feedback}",
        "en": "Scene {scene} reached the maximum retry limit ({max_retries}) in auto mode and still did not reach the target score. The system selected the highest-scoring video to continue the workflow. Best score: {score}. Feedback: {feedback}",
        "ja": "シーン{scene}は自動モードで最大リトライ回数（{max_retries}回）に達しても目標スコアに届きませんでした。ワークフロー継続のため、最高得点の動画を自動採用しました。最高スコア: {score}。フィードバック: {feedback}",
        "es": "La escena {scene} alcanzó el máximo de reintentos ({max_retries}) en modo automático y aun así no llegó a la puntuación objetivo. El sistema eligió automáticamente el video con mejor puntuación para continuar el flujo. Mejor puntuación: {score}. Comentario: {feedback}",
    },
    "message.video.manual_mode_keep_current": {
        "zh-CN": "分镜 {scene} 在手动模式下已完成审核，当前评分 {score} 分。不会自动重新生成，你可以人工继续下一步。参考反馈：{feedback}",
        "zh-TW": "分鏡 {scene} 在手動模式下已完成審核，目前評分 {score} 分。不會自動重新生成，你可以手動繼續下一步。參考回饋：{feedback}",
        "en": "Scene {scene} has been reviewed in manual mode with a current score of {score}. It will not be regenerated automatically, and you can manually continue to the next step. Feedback: {feedback}",
        "ja": "シーン{scene}は手動モードでレビュー済みです。現在のスコアは {score} です。自動再生成は行われず、手動で次のステップへ進めます。フィードバック: {feedback}",
        "es": "La escena {scene} ya fue revisada en modo manual con una puntuación actual de {score}. No se regenerará automáticamente y puedes continuar manualmente al siguiente paso. Comentario: {feedback}",
    },
    "progress.video.wait_user_confirmation": {
        "zh-CN": "分镜 {scene} 审核未通过，等待用户确认...",
        "zh-TW": "分鏡 {scene} 審核未通過，等待使用者確認...",
        "en": "Scene {scene} failed review. Waiting for user confirmation...",
        "ja": "シーン{scene}はレビュー不合格です。ユーザー確認待ちです...",
        "es": "La escena {scene} no aprobó la revisión. Esperando confirmación del usuario...",
    },
    "message.video.wait_user_confirmation": {
        "zh-CN": "分镜 {scene} 审核未通过 (评分: {score}分)。是否重新生成？(已重试 {retry}/{max_retries} 次)",
        "zh-TW": "分鏡 {scene} 審核未通過 (評分: {score}分)。是否重新生成？(已重試 {retry}/{max_retries} 次)",
        "en": "Scene {scene} failed review (score: {score}). Regenerate it? (retried {retry}/{max_retries} times)",
        "ja": "シーン{scene}はレビュー不合格です（スコア: {score}）。再生成しますか？（{retry}/{max_retries}回リトライ済み）",
        "es": "La escena {scene} no aprobó la revisión (puntuación: {score}). ¿Deseas regenerarla? (reintentos {retry}/{max_retries})",
    },
    "progress.merge.generating": {
        "zh-CN": "正在合成最终视频...",
        "zh-TW": "正在合成最終影片...",
        "en": "Merging the final video...",
        "ja": "最終動画を結合しています...",
        "es": "Uniendo el video final...",
    },
    "progress.merge.completed": {
        "zh-CN": "视频合成完成",
        "zh-TW": "影片合成完成",
        "en": "Video merge completed",
        "ja": "動画の結合が完了しました",
        "es": "La unión del video se completó",
    },
    "message.all_videos_completed": {
        "zh-CN": "🎉 视频生成全部完成！",
        "zh-TW": "🎉 影片生成全部完成！",
        "en": "🎉 All video generation steps are complete!",
        "ja": "🎉 すべての動画生成ステップが完了しました！",
        "es": "🎉 ¡Todas las etapas de generación de video se completaron!",
    },
    "error.generation_failed": {
        "zh-CN": "生成失败: {error}",
        "zh-TW": "生成失敗: {error}",
        "en": "Generation failed: {error}",
        "ja": "生成に失敗しました: {error}",
        "es": "La generación falló: {error}",
    },
    "error.video_generation_failed": {
        "zh-CN": "视频生成失败: {error}",
        "zh-TW": "影片生成失敗: {error}",
        "en": "Video generation failed: {error}",
        "ja": "動画生成に失敗しました: {error}",
        "es": "La generación de video falló: {error}",
    },
    "error.step_execute_failed": {
        "zh-CN": "步骤执行失败: {error}",
        "zh-TW": "步驟執行失敗: {error}",
        "en": "Step execution failed: {error}",
        "ja": "ステップの実行に失敗しました: {error}",
        "es": "La ejecución del paso falló: {error}",
    },
    "error.reference_missing": {
        "zh-CN": "未找到参考图，请先生成参考图",
        "zh-TW": "未找到參考圖，請先生成參考圖",
        "en": "Reference image not found. Please generate the reference image first.",
        "ja": "参照画像が見つかりません。先に参照画像を生成してください。",
        "es": "No se encontró la imagen de referencia. Primero genera la imagen de referencia.",
    },
    "error.merge_before_all_scenes_completed": {
        "zh-CN": "仍有未完成的分镜视频，不能直接进入合成。请先继续生成后续分镜。",
        "zh-TW": "仍有尚未完成的分鏡影片，不能直接進入合成。請先繼續生成後續分鏡。",
        "en": "Some scene videos are still unfinished, so merge cannot start yet. Please continue generating the remaining scenes first.",
        "ja": "まだ未完了のシーン動画があるため、合成を開始できません。先に残りのシーンを生成してください。",
        "es": "Todavia hay escenas sin terminar, por lo que no se puede iniciar la mezcla. Continúa generando las escenas restantes primero.",
    },
    "error.confirmation_timeout": {
        "zh-CN": "等待确认超时，请重新生成",
        "zh-TW": "等待確認逾時，請重新生成",
        "en": "Confirmation timed out. Please regenerate.",
        "ja": "確認待ちがタイムアウトしました。再生成してください。",
        "es": "Se agotó el tiempo de espera para la confirmación. Vuelve a generar.",
    },
    "error.project_access_denied": {
        "zh-CN": "项目 {project_id} 当前正在其他浏览器窗口中运行，请在对应窗口继续操作。",
        "zh-TW": "專案 {project_id} 目前正在其他瀏覽器視窗中執行，請在對應視窗繼續操作。",
        "en": "Project {project_id} is currently running in another browser window. Please continue in that window.",
        "ja": "プロジェクト {project_id} は現在別のブラウザーウィンドウで実行中です。該当のウィンドウで続行してください。",
        "es": "El proyecto {project_id} se está ejecutando actualmente en otra ventana del navegador. Continúa allí.",
    },
    "message.project.ended": {
        "zh-CN": "项目已结束，临时文件与虚拟素材库资源已开始清理。",
        "zh-TW": "專案已結束，臨時檔案與虛擬素材庫資源已開始清理。",
        "en": "The project has been ended, and cleanup of temporary files and virtual asset library resources has started.",
        "ja": "プロジェクトを終了しました。一時ファイルと仮想アセットライブラリのクリーンアップを開始しました。",
        "es": "El proyecto se ha finalizado y ya empezó la limpieza de archivos temporales y recursos de la biblioteca virtual de materiales.",
    },
    "error.project_end_failed": {
        "zh-CN": "结束项目失败：{error}",
        "zh-TW": "結束專案失敗：{error}",
        "en": "Failed to end project: {error}",
        "ja": "プロジェクトの終了に失敗しました: {error}",
        "es": "No se pudo finalizar el proyecto: {error}",
    },
    "error.no_active_websocket": {
        "zh-CN": "没有活跃的 WebSocket 连接，请刷新页面后重试。",
        "zh-TW": "沒有有效的 WebSocket 連線，請重新整理頁面後再試。",
        "en": "There is no active WebSocket connection. Please refresh the page and try again.",
        "ja": "有効な WebSocket 接続がありません。ページを再読み込みしてから再試行してください。",
        "es": "No hay una conexión WebSocket activa. Actualiza la página e inténtalo de nuevo.",
    },
    "error.invalid_client_id": {
        "zh-CN": "client_id 无效或连接已断开：{client_id}",
        "zh-TW": "client_id 無效或連線已中斷：{client_id}",
        "en": "The client_id is invalid or the connection has been closed: {client_id}",
        "ja": "client_id が無効か、接続が切断されています: {client_id}",
        "es": "El client_id no es válido o la conexión se cerró: {client_id}",
    },
    "error.project_not_found": {
        "zh-CN": "未找到项目",
        "zh-TW": "找不到專案",
        "en": "Project not found",
        "ja": "プロジェクトが見つかりません",
        "es": "No se encontró el proyecto",
    },
    "error.invalid_step": {
        "zh-CN": "无效的步骤：{step}",
        "zh-TW": "無效的步驟：{step}",
        "en": "Invalid step: {step}",
        "ja": "無効なステップです: {step}",
        "es": "Paso no válido: {step}",
    },
    "error.invalid_type": {
        "zh-CN": "无效的类型参数",
        "zh-TW": "無效的類型參數",
        "en": "Invalid type parameter",
        "ja": "type パラメーターが無効です",
        "es": "El parámetro type no es válido",
    },
    "error.reference_name_required": {
        "zh-CN": "第 {index} 张{type}参考图缺少名称，请先填写名称后再继续。",
        "zh-TW": "第 {index} 張{type}參考圖缺少名稱，請先填寫名稱後再繼續。",
        "en": "Reference image {index} for {type} is missing a name. Please enter a name before continuing.",
        "ja": "{type}参考画像の {index} 枚目に名前がありません。続行する前に名前を入力してください。",
        "es": "La imagen de referencia {index} de {type} no tiene nombre. Introduce un nombre antes de continuar.",
    },
    "error.reference_upload_limit_total": {
        "zh-CN": "最多只能上传 {count} 张参考图",
        "zh-TW": "最多只能上傳 {count} 張參考圖",
        "en": "You can upload up to {count} reference images.",
        "ja": "アップロードできる参照画像は最大 {count} 枚です。",
        "es": "Solo puedes subir hasta {count} imágenes de referencia.",
    },
    "error.reference_upload_limit_character": {
        "zh-CN": "人物/角色参考图最多只能上传 {count} 张",
        "zh-TW": "人物/角色參考圖最多只能上傳 {count} 張",
        "en": "You can upload up to {count} character reference images.",
        "ja": "アップロードできるキャラクター参照画像は最大 {count} 枚です。",
        "es": "Solo puedes subir hasta {count} imágenes de referencia de personajes.",
    },
    "error.reference_upload_limit_scene": {
        "zh-CN": "布景参考图最多只能上传 {count} 张",
        "zh-TW": "布景參考圖最多只能上傳 {count} 張",
        "en": "You can upload up to {count} backdrop reference images.",
        "ja": "アップロードできる背景セット参照画像は最大 {count} 枚です。",
        "es": "Solo puedes subir hasta {count} imágenes de referencia de escenografía.",
    },
    "error.missing_project_or_target_step": {
        "zh-CN": "缺少 project_id 或 target_step",
        "zh-TW": "缺少 project_id 或 target_step",
        "en": "Missing project_id or target_step",
        "ja": "project_id または target_step が不足しています",
        "es": "Falta project_id o target_step",
    },
    "error.reference_regeneration_locked": {
        "zh-CN": "视频生成已经开始，参考图重新生成已锁定",
        "zh-TW": "影片生成已開始，參考圖重新生成已鎖定",
        "en": "Video generation has already started, so reference image regeneration is locked.",
        "ja": "動画生成はすでに開始されているため、参照画像の再生成はロックされています。",
        "es": "La generación de video ya comenzó, por lo que la regeneración de la imagen de referencia está bloqueada.",
    },
    "error.reference_regeneration_locked_original": {
        "zh-CN": "当前参考图来自“使用原图”，因此不支持重新生成参考图。",
        "zh-TW": "目前參考圖來自「使用原圖」，因此不支援重新生成參考圖。",
        "en": "The current reference image comes from Use Original Image, so reference image regeneration is not available.",
        "ja": "現在の参照画像は「元画像を使用」から作成されているため、参照画像の再生成は利用できません。",
        "es": "La imagen de referencia actual proviene de Usar imagen original, por lo que no se puede regenerar la imagen de referencia.",
    },
    "error.no_scenes_remaining_after_skip": {
        "zh-CN": "所有分镜都已被跳过，当前没有可继续生成或合成的分镜视频。",
        "zh-TW": "所有分鏡都已被跳過，目前沒有可繼續生成或合成的分鏡影片。",
        "en": "All scenes have been skipped. There are no remaining storyboard videos to generate or merge.",
        "ja": "すべてのシーンがスキップされました。生成または結合できる絵コンテ動画は残っていません。",
        "es": "Se omitieron todas las escenas. No quedan videos del storyboard para generar o unir.",
    },
    "error.reference_generation_failed_after_retries": {
        "zh-CN": "参考图库生成失败，已自动重试 {retries} 次：{error}",
        "zh-TW": "參考圖庫生成失敗，已自動重試 {retries} 次：{error}",
        "en": "Reference library generation failed after {retries} automatic retries: {error}",
        "ja": "参照画像ライブラリの生成に失敗しました。自動再試行回数 {retries} 回を使い切りました: {error}",
        "es": "La generación de la biblioteca de referencias falló tras {retries} reintentos automáticos: {error}",
    },
    "error.unsupported_reference_type": {
        "zh-CN": "不支持的参考图类型：{reference_type}",
        "zh-TW": "不支援的參考圖類型：{reference_type}",
        "en": "Unsupported reference image type: {reference_type}",
        "ja": "未対応の参照画像タイプです: {reference_type}",
        "es": "Tipo de imagen de referencia no compatible: {reference_type}",
    },
    "error.reference_asset_name_required": {
        "zh-CN": "参考图名称不能为空",
        "zh-TW": "參考圖名稱不能為空",
        "en": "Reference image name is required",
        "ja": "参照画像名は必須です",
        "es": "El nombre de la imagen de referencia es obligatorio",
    },
    "error.reference_asset_target_required": {
        "zh-CN": "参考图重新生成必须指定被点击的那一张参考图，不能整套一起重新生成。",
        "zh-TW": "重新生成參考圖時必須指定被點擊的那一張參考圖，不能整套一起重新生成。",
        "en": "Reference image regeneration must target the clicked item only, not the entire reference library.",
        "ja": "参照画像の再生成では、クリックした画像を指定する必要があります。参照画像ライブラリ全体は再生成できません。",
        "es": "La regeneración de imágenes de referencia debe apuntar solo al elemento seleccionado, no a toda la biblioteca de referencias.",
    },
    "error.reference_asset_not_found": {
        "zh-CN": "未找到参考图：{reference_type}/{name}",
        "zh-TW": "找不到參考圖：{reference_type}/{name}",
        "en": "Reference image not found: {reference_type}/{name}",
        "ja": "参照画像が見つかりません: {reference_type}/{name}",
        "es": "No se encontró la imagen de referencia: {reference_type}/{name}",
    },
    "error.reference_character_not_found": {
        "zh-CN": "未找到角色设定：{name}",
        "zh-TW": "找不到角色設定：{name}",
        "en": "Character definition not found: {name}",
        "ja": "キャラクター設定が見つかりません: {name}",
        "es": "No se encontró la definición del personaje: {name}",
    },
    "error.reference_scene_not_found": {
        "zh-CN": "未找到布景设定：{name}",
        "zh-TW": "找不到布景設定：{name}",
        "en": "Backdrop definition not found: {name}",
        "ja": "背景セット定義が見つかりません: {name}",
        "es": "No se encontró la definición de escenografía: {name}",
    },
    "review.output_language_rule": {
        "zh-CN": "反馈 feedback 必须使用{language}输出；JSON 字段名必须保持英文不变。",
        "zh-TW": "回饋 feedback 必須使用{language}輸出；JSON 欄位名必須保持英文不變。",
        "en": "The feedback field must be written in {language}; JSON field names must remain in English.",
        "ja": "feedback フィールドは{language}で出力し、JSON のフィールド名は英語のまま維持してください。",
        "es": "El campo feedback debe escribirse en {language}; los nombres de los campos JSON deben mantenerse en inglés.",
    },
    "script.output_language_rule": {
        "zh-CN": "除 JSON 字段名外，所有人类可读内容必须使用{language}输出。JSON 字段名必须保持为 title、style、background、characters、scenes 等英文键名。",
        "zh-TW": "除 JSON 欄位名外，所有人類可讀內容必須使用{language}輸出。JSON 欄位名必須保持為 title、style、background、characters、scenes 等英文鍵名。",
        "en": "All human-readable values must be written in {language}. JSON field names must remain in English, such as title, style, background, characters, and scenes.",
        "ja": "JSON のフィールド名以外の人間向けテキストはすべて{language}で出力してください。フィールド名は title、style、background、characters、scenes など英語のままにしてください。",
        "es": "Todos los valores legibles para humanos deben escribirse en {language}. Los nombres de los campos JSON deben mantenerse en inglés, como title, style, background, characters y scenes.",
    },
    "label.reference_type_character": {
        "zh-CN": "人物/角色",
        "zh-TW": "人物/角色",
        "en": "character",
        "ja": "キャラクター",
        "es": "personaje",
    },
    "label.reference_type_scene": {
        "zh-CN": "布景",
        "zh-TW": "布景",
        "en": "backdrop",
        "ja": "背景セット",
        "es": "escenografia",
    },
}


def normalize_locale(locale: Optional[str]) -> str:
    if not locale:
        return "zh-CN"
    if locale in SUPPORTED_OUTPUT_LANGUAGES:
        return locale
    lower = locale.lower()
    if lower.startswith("zh-cn"):
        return "zh-CN"
    if lower.startswith("zh-tw"):
        return "zh-TW"
    if lower.startswith("en"):
        return "en"
    if lower.startswith("ja"):
        return "ja"
    if lower.startswith("es"):
        return "es"
    return "zh-CN"


def language_name(locale: Optional[str]) -> str:
    return SUPPORTED_OUTPUT_LANGUAGES.get(normalize_locale(locale), "简体中文")


def translate(locale: Optional[str], key: str, **kwargs) -> str:
    normalized = normalize_locale(locale)
    template = BACKEND_TRANSLATIONS.get(key, {}).get(normalized)
    if template is None:
        template = BACKEND_TRANSLATIONS.get(key, {}).get("zh-CN", key)
    return template.format(**kwargs)
