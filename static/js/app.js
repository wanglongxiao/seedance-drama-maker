/*
 * Copyright (c) 2026 Alex Wang
 * @author Alex Wang <https://github.com/wanglongxiao>
 * @contact https://www.linkedin.com/in/alexwanglx/
 * Open Source Usage: attribution required; preserve this notice in redistributions.
 */

// 全局状态
let currentProjectId = null;
// 上传图片/音频会调用 ensureDraftProjectId() 提前生成「草稿」projectId，
// 此时后端尚未创建真实项目。用该标志区分草稿态与已创建态，避免把草稿 ID
// 误判为「已有项目」而导致 auto 提示无法触发全自动模式。
let hasDraftProject = false;
let uploadedImages = [];
let uploadedAudio = null;
let ws = null;
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let heartbeatInterval = null;
let currentStep = null; // 当前步骤: script, images, videos, merge
let isWaitingForConfirm = false; // 是否正在等待用户确认
let pendingStep = null; // 待执行的下一步
let overallProgress = 0; // 整体进度
let stepProgress = { // 各步骤进度
    script: 0,
    reference_image: 0,
    videos: 0,
    merge: 0
};
// 视频分镜进度跟踪（用于“videos”步骤的真实进度展示）
let videoTotalScenes = null;
let videoSceneState = {}; // { [sceneNumber]: { generated: bool, reviewed: bool } }
let isAutoRunMode = false; // 是否处于全自动模式
let autoRunNextStep = null; // 全自动模式下待执行的下一步
let autoRunCountdown = null; // 倒计时器
let autoRunCountdownValue = 0; // 倒计时剩余秒数
let wsClientId = null; // 后端分配的WebSocket client_id（用于HTTP接口路由到正确连接）
let currentLanguage = 'zh-CN';
let translations = {};
let lastScriptData = null;
let lastReferenceImageOutput = null;
let lastImagesOutput = null;
let lastFinalVideoUrl = null;
let lastComicPdfOutput = null;
let mergeStepVisible = false;
let videoOutputsByScene = {};
let videoReviewOutputsByScene = {};
let lastStatusState = null;
let referenceImageLocked = false;
let referenceImageRegenerating = false;
let regeneratingReferenceAssetKeys = new Set();
let regeneratingVideoSceneNumbers = new Set();
let useOriginalReference = false;
let referenceImageRegenerateLocked = false;
// 参考图分阶段（category1/category2/category3）子状态机：
let referenceStage = null; // 当前已完成子阶段：category1/category2/category3
let pendingReferenceStage = null; // 待进入子阶段：category2/category3/videos
let referenceStageHasCategory2 = false; // 后端下发：本项目是否存在分类2（装扮/布景状态）
let projectEnding = false;
let projectEnded = false;
let projectEndBeaconSent = false;

// 刷新恢复：把「当前正在运行的真实项目」持久化到 sessionStorage（每个 Tab 独立）。
// - 同一 Tab 刷新：sessionStorage 保留，恢复项目并继续，不新建项目。
// - 真正关闭 Tab：sessionStorage 随之清空，不影响其它 Tab。
// - 仅在后端确认真实项目（非草稿）时写入；「结束项目」时清除。
const PERSISTED_PROJECT_KEY = 'active_project_id';

function persistActiveProject(projectId) {
    try {
        if (projectId) {
            sessionStorage.setItem(PERSISTED_PROJECT_KEY, projectId);
        } else {
            sessionStorage.removeItem(PERSISTED_PROJECT_KEY);
        }
    } catch (e) {
        // sessionStorage 不可用时忽略：退化为「刷新后需重新开始」，不影响主流程。
    }
}

function getPersistedProjectId() {
    try {
        return sessionStorage.getItem(PERSISTED_PROJECT_KEY) || null;
    } catch (e) {
        return null;
    }
}
const I18N_VERSION = '20260818c';
const FRONTEND_CONFIG_VERSION = '20260811c';
const SUPPORTED_UI_LANGUAGES = new Set(['zh-CN', 'zh-TW', 'en', 'ja', 'es']);
const UI_LANGUAGE_ALIASES = {
    'zh-cn': 'zh-CN',
    'zh_cn': 'zh-CN',
    'zh-hans': 'zh-CN',
    'zh': 'zh-CN',
    '简体中文': 'zh-CN',
    '簡體中文': 'zh-CN',
    '中文简体': 'zh-CN',
    '中文簡體': 'zh-CN',
    'simplified chinese': 'zh-CN',
    'chinese simplified': 'zh-CN',
    'zh-tw': 'zh-TW',
    'zh_tw': 'zh-TW',
    'zh-hant': 'zh-TW',
    '繁體中文': 'zh-TW',
    '繁体中文': 'zh-TW',
    'traditional chinese': 'zh-TW',
    'chinese traditional': 'zh-TW',
    'en': 'en',
    'en-us': 'en',
    'en_us': 'en',
    'en-gb': 'en',
    'english': 'en',
    'ja': 'ja',
    'ja-jp': 'ja',
    'ja_jp': 'ja',
    '日本語': 'ja',
    'japanese': 'ja',
    'es': 'es',
    'es-es': 'es',
    'es_es': 'es',
    'español': 'es',
    'espanol': 'es',
    'spanish': 'es'
};
let languageRequestSerial = 0;
let frontendConfig = {
    auto_run_countdown_seconds: 10,
    reference_image_max_count: 30,
    character_reference_max_count: 10,
    scene_reference_max_count: 20,
};

function getCurrentReviewMode() {
    return isAutoRunMode ? 'auto' : 'manual';
}

function getCurrentVideoMode() {
    const select = document.getElementById('videoModeSelect');
    const value = select ? String(select.value || '').trim().toLowerCase() : '';
    return value === 'extend' ? 'extend' : 'parallel';
}

// ===== 自定义下拉框：接管原生 select 的弹层，保证弹出位置贴合触发器、字体清晰、跨端一致 =====
const customSelectRegistry = new Map();

function enhanceSelect(selectId) {
    const select = document.getElementById(selectId);
    if (!select || customSelectRegistry.has(selectId)) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'custom-select';

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'custom-select-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');

    const valueEl = document.createElement('span');
    valueEl.className = 'custom-select-value';

    const arrowEl = document.createElement('span');
    arrowEl.className = 'custom-select-arrow';
    arrowEl.textContent = '▾';

    trigger.appendChild(valueEl);
    trigger.appendChild(arrowEl);

    const menu = document.createElement('ul');
    menu.className = 'custom-select-menu';
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;

    // 将原生 select 隐藏（保留在 DOM 中作为状态源），并把自定义 UI 放到其后
    select.classList.add('native-select-hidden');
    select.parentNode.insertBefore(wrapper, select.nextSibling);
    wrapper.appendChild(select);
    wrapper.appendChild(trigger);
    wrapper.appendChild(menu);

    const state = { select, wrapper, trigger, valueEl, menu };
    customSelectRegistry.set(selectId, state);

    const closeMenu = () => {
        menu.hidden = true;
        wrapper.classList.remove('open');
        trigger.setAttribute('aria-expanded', 'false');
    };
    const openMenu = () => {
        // 打开前关闭其它已展开的自定义下拉
        customSelectRegistry.forEach((s) => {
            if (s !== state) {
                s.menu.hidden = true;
                s.wrapper.classList.remove('open');
                s.trigger.setAttribute('aria-expanded', 'false');
            }
        });
        rebuildOptions(selectId);
        menu.hidden = false;
        wrapper.classList.add('open');
        trigger.setAttribute('aria-expanded', 'true');
    };

    trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        if (menu.hidden) openMenu(); else closeMenu();
    });

    // 点击页面其它区域关闭
    document.addEventListener('click', (e) => {
        if (!wrapper.contains(e.target)) closeMenu();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeMenu();
    });

    state.closeMenu = closeMenu;
    rebuildOptions(selectId);
    syncSelectDisplay(selectId);
}

function rebuildOptions(selectId) {
    const state = customSelectRegistry.get(selectId);
    if (!state) return;
    const { select, menu } = state;
    menu.innerHTML = '';
    Array.from(select.options).forEach((opt) => {
        const li = document.createElement('li');
        li.className = 'custom-select-option';
        li.setAttribute('role', 'option');
        li.dataset.value = opt.value;
        li.textContent = opt.textContent;
        if (opt.value === select.value) li.classList.add('selected');
        li.addEventListener('click', (e) => {
            e.stopPropagation();
            if (select.value !== opt.value) {
                select.value = opt.value;
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }
            syncSelectDisplay(selectId);
            state.closeMenu();
        });
        menu.appendChild(li);
    });
}

function syncSelectDisplay(selectId) {
    const state = customSelectRegistry.get(selectId);
    if (!state) return;
    const { select, valueEl, menu } = state;
    const selected = select.options[select.selectedIndex];
    valueEl.textContent = selected ? selected.textContent : '';
    menu.querySelectorAll('.custom-select-option').forEach((li) => {
        li.classList.toggle('selected', li.dataset.value === select.value);
    });
}

function initCustomSelects() {
    enhanceSelect('languageSelect');
    enhanceSelect('videoModeSelect');
}

function refreshCustomSelects() {
    customSelectRegistry.forEach((_, id) => {
        rebuildOptions(id);
        syncSelectDisplay(id);
    });
}


function ensureDraftProjectId() {
    if (currentProjectId) return currentProjectId;
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        currentProjectId = window.crypto.randomUUID().slice(0, 8);
    } else {
        currentProjectId = `p${Math.random().toString(36).slice(2, 10)}`;
    }
    // 标记为草稿态：后端尚未据此创建真实项目。
    hasDraftProject = true;
    projectEnded = false;
    projectEndBeaconSent = false;
    updateProjectActionState();
    return currentProjectId;
}

function getUploadedImageUrls() {
    return uploadedImages.map((item) => item.url);
}

function getUploadedImageAssets() {
    return uploadedImages.map((item) => ({
        url: item.url,
        reference_type: item.reference_type || 'character',
        name: String(item.name || '').trim()
    }));
}

function countUploadedImagesByType(referenceType) {
    return uploadedImages.filter((item) => (item.reference_type || 'character') === referenceType).length;
}

function getMissingUploadedImageNameLabels() {
    return uploadedImages
        .map((item, index) => {
            const name = String(item?.name || '').trim();
            if (name) return null;
            const referenceType = item?.reference_type === 'scene' ? 'scene' : 'character';
            const typeLabel = referenceType === 'scene'
                ? t('labels.referenceTypeScene')
                : t('labels.referenceTypeCharacter');
            return `${t('labels.image', { index: index + 1 })} (${typeLabel})`;
        })
        .filter(Boolean);
}

function validateUploadedImageNames() {
    const missingLabels = getMissingUploadedImageNameLabels();
    if (!missingLabels.length) return true;
    alert(t('messages.referenceNameRequired', { items: missingLabels.join('、') }));
    updateUploadedFiles();
    return false;
}

// DOM 元素
const appTitle = document.getElementById('appTitle');
const appSubtitle = document.getElementById('appSubtitle');
const languageLabel = document.getElementById('languageLabel');
const languageSelect = document.getElementById('languageSelect');
const chatMessages = document.getElementById('chatMessages');
const textInput = document.getElementById('textInput');
const sendBtn = document.getElementById('sendBtn');
const imageBtn = document.getElementById('imageBtn');
const imageInput = document.getElementById('imageInput');
const micBtn = document.getElementById('micBtn');
const recordingIndicator = document.getElementById('recordingIndicator');
const uploadedFiles = document.getElementById('uploadedFiles');
const contentDisplay = document.getElementById('contentDisplay');
const emptyState = document.getElementById('emptyState');
const emptyStateText = document.getElementById('emptyStateText');
const statusSection = document.getElementById('statusSection');
const statusText = document.getElementById('statusText');
const loadingOverlay = document.getElementById('loadingOverlay');
const loadingText = document.getElementById('loadingText');
const overallProgressTitle = document.getElementById('overallProgressTitle');
const stepScriptLabel = document.getElementById('stepScriptLabel');
const stepReferenceLabel = document.getElementById('stepReferenceLabel');
const stepVideosLabel = document.getElementById('stepVideosLabel');
const stepMergeLabel = document.getElementById('stepMergeLabel');
const endProjectBtn = document.getElementById('endProjectBtn');

// 媒体弹窗元素
const mediaModal = document.getElementById('mediaModal');
const mediaModalClose = document.getElementById('mediaModalClose');
const mediaModalBody = document.getElementById('mediaModalBody');

function getExternalLinkAttrs() {
    return 'target="_blank" rel="noopener noreferrer"';
}

function getNestedTranslation(path) {
    return path.split('.').reduce((acc, key) => (acc && acc[key] !== undefined ? acc[key] : undefined), translations);
}

function t(key, vars = {}) {
    const value = getNestedTranslation(key);
    if (typeof value !== 'string') return key;
    return value.replace(/\{(\w+)\}/g, (_, name) => (
        vars[name] !== undefined && vars[name] !== null ? String(vars[name]) : ''
    ));
}

function getHttpErrorMessage(status) {
    return t('messages.httpErrorStatus', { status });
}

async function loadTranslations(locale) {
    const response = await fetch(`/static/i18n/${locale}.json?v=${I18N_VERSION}`);
    if (!response.ok) {
        throw new Error(`Failed to load locale: ${locale}`);
    }
    return response.json();
}

function normalizeUiLanguage(locale) {
    if (!locale) return 'zh-CN';
    if (SUPPORTED_UI_LANGUAGES.has(locale)) return locale;

    const normalized = String(locale).trim().toLowerCase();
    if (UI_LANGUAGE_ALIASES[normalized]) {
        return UI_LANGUAGE_ALIASES[normalized];
    }
    if (normalized.startsWith('zh-cn') || normalized.startsWith('zh-hans')) {
        return 'zh-CN';
    }
    if (normalized.startsWith('zh-tw') || normalized.startsWith('zh-hant')) {
        return 'zh-TW';
    }
    if (normalized.startsWith('en')) {
        return 'en';
    }
    if (normalized.startsWith('ja')) {
        return 'ja';
    }
    if (normalized.startsWith('es')) {
        return 'es';
    }
    return 'zh-CN';
}

async function loadFrontendConfig() {
    try {
        const response = await fetch(`/api/frontend-config?v=${FRONTEND_CONFIG_VERSION}`);
        if (!response.ok) {
            throw new Error(`Failed to load frontend config: ${response.status}`);
        }
        const result = await response.json();
        if (result && result.success && result.config) {
            frontendConfig = {
                ...frontendConfig,
                ...result.config,
            };
            applyDefaultVideoMode();
        }
    } catch (error) {
        console.error('Failed to load frontend config:', error);
    }
}

function applyDefaultVideoMode() {
    const select = document.getElementById('videoModeSelect');
    if (!select) return;
    const defaultMode = frontendConfig.default_video_generation_mode === 'extend' ? 'extend' : 'parallel';
    select.value = defaultMode;
    if (typeof syncSelectDisplay === 'function' && customSelectRegistry.has('videoModeSelect')) {
        syncSelectDisplay('videoModeSelect');
    }
}

function setDocumentLanguage() {
    document.documentElement.lang = currentLanguage;
    document.title = t('meta.pageTitle');
}

function getWelcomeMessageHtml() {
    return `
        <div class="message agent-message">
            <div class="message-avatar">🤖</div>
            <div class="message-content">
                <p>${t('app.welcome')}</p>
                <p>${t('app.youCan')}</p>
                <ul>
                    <li>${t('app.welcomeText')}</li>
                    <li>${t('app.welcomeUpload')}</li>
                    <li>${t('app.welcomeRecord')}</li>
                </ul>
                <p style="margin-top: 10px; color: #1890ff; font-size: 13px;">
                    💡 <strong>${t('app.tipTitle')}</strong>${t('app.tipText')}<br>
                    🤖 <strong>${t('app.quickCommandsTitle')}</strong>${t('app.quickCommandsText')}
                </p>
            </div>
        </div>
    `;
}

function renderWelcomeMessage(force = false) {
    if (!chatMessages) return;
    if (!force && chatMessages.children.length > 0) return;
    chatMessages.innerHTML = getWelcomeMessageHtml();
}

function applyStaticTranslations() {
    setDocumentLanguage();
    if (appTitle) appTitle.innerHTML = `🎬 ${t('app.title')}`;
    if (appSubtitle) appSubtitle.textContent = t('app.subtitle');
    if (languageLabel) languageLabel.textContent = t('app.language');
    const videoModeLabel = document.getElementById('videoModeLabel');
    if (videoModeLabel) videoModeLabel.textContent = t('app.videoModeLabel');
    const videoModeSelect = document.getElementById('videoModeSelect');
    if (videoModeSelect && videoModeSelect.options.length >= 2) {
        videoModeSelect.options[0].textContent = t('app.videoModeParallel');
        videoModeSelect.options[1].textContent = t('app.videoModeExtend');
    }
    if (imageBtn) imageBtn.title = t('input.uploadImage');
    if (micBtn) micBtn.title = isRecording ? t('input.stopRecording') : t('input.holdToRecord');
    if (textInput) textInput.placeholder = t('input.placeholder');
    if (sendBtn) sendBtn.textContent = t('input.send');
    if (recordingIndicator) recordingIndicator.lastChild.textContent = ` ${t('input.recording')}`;
    if (overallProgressTitle) overallProgressTitle.textContent = t('progress.overall');
    if (stepScriptLabel) stepScriptLabel.textContent = t('steps.script');
    if (stepReferenceLabel) stepReferenceLabel.textContent = t('steps.referenceImage');
    if (stepVideosLabel) stepVideosLabel.textContent = t('steps.videos');
    if (stepMergeLabel) stepMergeLabel.textContent = t('steps.merge');
    const currentEmptyStateText = document.getElementById('emptyStateText');
    if (currentEmptyStateText) currentEmptyStateText.textContent = t('progress.emptyState');
    if (statusSection && statusSection.style.display === 'none') {
        statusSection.innerHTML = `<p class="status-text" id="statusText">${t('progress.waiting')}</p>`;
    }
    updateProjectActionState();
    refreshCustomSelects();
}

function rerenderPreviewCards() {
    const scriptCard = document.getElementById('script-card');
    if (scriptCard) scriptCard.remove();
    const referenceCard = document.getElementById('reference-image-card');
    if (referenceCard) referenceCard.remove();
    const storyboardCard = document.getElementById('storyboard-card');
    if (storyboardCard) storyboardCard.remove();
    const comicPdfCard = document.getElementById('comic-pdf-card');
    if (comicPdfCard) comicPdfCard.remove();
    const imagesCard = document.getElementById('images-card');
    if (imagesCard) imagesCard.remove();
    const videosContainer = document.getElementById('videos-container');
    if (videosContainer) videosContainer.remove();
    const mergeCard = document.getElementById('merge-step-card');
    if (mergeCard) mergeCard.remove();
    const finalVideoCard = document.getElementById('final-video-card');
    if (finalVideoCard) finalVideoCard.remove();

    if (lastScriptData) {
        displayScript(lastScriptData);
    }
    if (lastImagesOutput) {
        displayImages(lastImagesOutput);
    } else if (lastReferenceImageOutput) {
        displayReferenceImage(lastReferenceImageOutput);
    }
    if (lastComicPdfOutput) {
        displayComicPdfLink(lastComicPdfOutput);
    }

    Object.values(videoOutputsByScene)
        .sort((a, b) => (a.scene_number || 0) - (b.scene_number || 0))
        .forEach((output) => displayVideos(output));

    Object.values(videoReviewOutputsByScene)
        .sort((a, b) => (a.scene_number || 0) - (b.scene_number || 0))
        .forEach((output) => displayVideoReviewResult(output));

    if (mergeStepVisible && !lastFinalVideoUrl) {
        showMergeStep();
    }
    if (lastFinalVideoUrl) {
        displayFinalVideo(lastFinalVideoUrl);
    }
}

function rerenderLocalizedUI() {
    applyStaticTranslations();
    if (!chatMessages.querySelector('.message.user-message') && chatMessages.children.length <= 1) {
        renderWelcomeMessage(true);
    }
    updateUploadedFiles();
    rerenderPreviewCards();
}

async function setLanguage(locale, shouldRerender = true) {
    const normalizedLocale = normalizeUiLanguage(locale);
    const requestSerial = ++languageRequestSerial;
    let loadedTranslations = null;
    let resolvedLanguage = normalizedLocale;

    try {
        loadedTranslations = await loadTranslations(normalizedLocale);
    } catch (error) {
        console.error('Failed to load translations:', error);
        if (normalizedLocale !== 'zh-CN') {
            loadedTranslations = await loadTranslations('zh-CN');
            resolvedLanguage = 'zh-CN';
        }
    }

    if (requestSerial !== languageRequestSerial || !loadedTranslations) {
        return;
    }

    translations = loadedTranslations;
    currentLanguage = resolvedLanguage;

    localStorage.setItem('uiLanguage', currentLanguage);
    if (languageSelect) {
        languageSelect.value = currentLanguage;
    }
    if (ws && ws.readyState === WebSocket.OPEN && currentProjectId) {
        ws.send(JSON.stringify({
            type: 'set_language',
            project_id: currentProjectId,
            ui_language: currentLanguage
        }));
    }

    if (shouldRerender) {
        rerenderLocalizedUI();
    } else {
        applyStaticTranslations();
    }
}

async function initI18n() {
    const savedLanguage = localStorage.getItem('uiLanguage') || 'zh-CN';
    await setLanguage(savedLanguage, false);
}

function renderStatusBar(text, mode = 'info', stepName = '') {
    if (!statusSection) return;

    statusSection.dataset.mode = mode;
    statusSection.style.display = 'flex';

    if (mode === 'loading') {
        const stepPrefix = stepName ? `<span style="font-weight: 600; color: #1677ff;">${stepName}</span><span style="color:#999;"> / </span>` : '';
        statusSection.innerHTML = `
            <div style="display:flex; align-items:center; justify-content:center; gap:10px;">
                <div class="item-generating-spinner" style="width:18px; height:18px; margin-bottom:0;"></div>
                <p class="status-text" id="statusText" style="margin:0;">${stepPrefix}${text || t('status.processing')}</p>
            </div>
        `;
    } else {
        statusSection.innerHTML = `<p class="status-text" id="statusText">${text || ''}</p>`;
    }
}

function hasActiveProjectContext() {
    return !!currentProjectId && !projectEnded;
}

function updateProjectActionState() {
    if (!endProjectBtn) return;
    const visible = !!currentProjectId && !projectEnded;
    endProjectBtn.style.display = visible ? 'inline-flex' : 'none';
    endProjectBtn.disabled = projectEnding;
    endProjectBtn.textContent = projectEnding ? t('actions.endingProject') : t('actions.endProject');
}

function buildProjectEndFormData(reason = 'user_end') {
    const formData = new FormData();
    formData.append('client_id', wsClientId || '');
    formData.append('reason', reason);
    formData.append('ui_language', currentLanguage);
    return formData;
}

function sendProjectEndBeacon(reason = 'browser_close') {
    if (!hasActiveProjectContext() || projectEnding || projectEndBeaconSent) {
        return false;
    }
    const projectId = currentProjectId;
    if (!projectId) return false;

    projectEndBeaconSent = true;
    const url = `/project/${encodeURIComponent(projectId)}/end`;
    const formData = buildProjectEndFormData(reason);

    if (navigator.sendBeacon) {
        return navigator.sendBeacon(url, formData);
    }

    fetch(url, {
        method: 'POST',
        body: formData,
        keepalive: true,
    }).catch((error) => {
        console.error('Project end beacon fallback failed:', error);
    });
    return true;
}

async function endProject(reason = 'user_end', options = {}) {
    const { skipConfirm = false, resetAfter = true, silent = false } = options;

    if (!hasActiveProjectContext()) {
        if (!silent) {
            addAgentMessage(t('messages.projectEndNoActive'));
        }
        return false;
    }

    if (!skipConfirm && !window.confirm(t('messages.projectEndConfirm'))) {
        return false;
    }

    const projectId = currentProjectId;
    projectEnding = true;
    projectEnded = false;
    updateProjectActionState();
    cancelAutoRunCountdown();
    renderStatusBar(t('messages.projectEnding'), 'loading', t('actions.endProject'));

    try {
        const response = await fetch(`/project/${encodeURIComponent(projectId)}/end`, {
            method: 'POST',
            body: buildProjectEndFormData(reason),
        });
        // 云端 API 网关在实例繁忙/超时时可能返回明文（如 "upstream request timeout"），
        // 直接 response.json() 会抛 "Unexpected token 'u'"。改用健壮解析。
        const result = await parseJsonResponse(response);

        if (!response.ok || !result.success) {
            throw new Error(result.error || getHttpErrorMessage(response.status));
        }

        projectEnding = false;
        projectEnded = true;
        projectEndBeaconSent = true;
        persistActiveProject(null); // 已结束：清除刷新恢复标记
        updateProjectActionState();

        if (resetAfter) {
            resetProject();
        } else {
            hideStatusSection();
        }

        if (!silent) {
            addAgentMessage(t('messages.projectEnded'));
        }
        return true;
    } catch (error) {
        console.error('End project error:', error);
        projectEnding = false;
        projectEnded = false;
        projectEndBeaconSent = false;
        updateProjectActionState();
        renderStatusBar(t('messages.projectEndFailed', { error: error.message || t('labels.unknown') }), 'info', t('actions.endProject'));
        if (!silent) {
            addAgentMessage(t('messages.projectEndFailed', { error: error.message || t('labels.unknown') }));
        }
        return false;
    }
}

function removeAutoRunCountdownMessage() {
    const countdownMsg = document.getElementById('autoRunCountdownMessage');
    if (countdownMsg) {
        countdownMsg.remove();
    }
}

function cancelAutoRunCountdown() {
    if (autoRunCountdown) {
        clearInterval(autoRunCountdown);
        autoRunCountdown = null;
    }
    removeAutoRunCountdownMessage();
}

function buildReferenceAssetKey(referenceType, referenceName) {
    return `${String(referenceType || '').trim().toLowerCase()}::${String(referenceName || '').trim()}`;
}

function canRegenerateReferenceImage(itemLocked = false, assetKey = '') {
    return !!currentProjectId && !referenceImageLocked && !itemLocked && !regeneratingReferenceAssetKeys.has(assetKey);
}

function canStartReferenceStepCountdown(output) {
    return !!(output && output.ready_for_confirmation === true);
}

function hasPendingReferenceRegeneration() {
    return referenceImageRegenerating || regeneratingReferenceAssetKeys.size > 0;
}

function hasPendingVideoGenerationTasks() {
    return regeneratingVideoSceneNumbers.size > 0;
}

function canStartPendingStepCountdown(completedStep) {
    if (completedStep === 'reference_image') {
        return pendingStep === 'videos'
            && canStartReferenceStepCountdown(lastReferenceImageOutput)
            && !referenceImageLocked
            && !hasPendingReferenceRegeneration();
    }
    if (completedStep === 'videos') {
        return pendingStep === 'merge' && !hasPendingVideoGenerationTasks();
    }
    return true;
}

function showPendingStepPrompt(step, nextStep) {
    isWaitingForConfirm = true;
    pendingStep = nextStep;
    if (step === 'reference_image') {
        addAgentMessage(t('messages.referenceComplete'));
    } else if (step === 'script') {
        addAgentMessage(t('messages.scriptComplete'));
    } else if (step === 'videos') {
        addAgentMessage(t('messages.videosComplete'));
    } else {
        showStatusSection(t('messages.genericStepCompleteStatus', { step: getStepName(step), nextStep: getStepName(nextStep) }));
        addAgentMessage(t('messages.genericStepComplete', { step: getStepName(step), nextStep: getStepName(nextStep) }));
    }
}

function maybeStartPendingStepCountdown() {
    if (!pendingStep || autoRunCountdown) return;
    if (pendingStep === 'videos' && canStartPendingStepCountdown('reference_image')) {
        showAutoRunCountdown('videos', 'reference_image', isAutoRunMode);
        return;
    }
    if (pendingStep === 'merge' && canStartPendingStepCountdown('videos')) {
        showAutoRunCountdown('merge', 'videos', isAutoRunMode);
    }
}

function refreshReferenceImageActionState() {
    const card = document.getElementById('reference-image-card');
    if (!card) return;
    const regenerateButtons = card.querySelectorAll('button.reference-regenerate-btn');
    regenerateButtons.forEach((regenerateBtn) => {
        const itemLocked = regenerateBtn.dataset.locked === 'true';
        const assetKey = regenerateBtn.dataset.referenceKey || '';
        if (itemLocked) {
            regenerateBtn.style.display = 'none';
            return;
        }
        regenerateBtn.style.display = 'inline-block';
        const enabled = canRegenerateReferenceImage(itemLocked, assetKey);
        regenerateBtn.disabled = !enabled;
        regenerateBtn.style.opacity = enabled ? '' : '0.65';
        regenerateBtn.style.cursor = enabled ? 'pointer' : 'not-allowed';
    });
}

// 统一刷新“参考图库/角色装扮图/布景状态图/各分镜故事版”四个模块的重新生成按钮状态。
// 视频生成开始（referenceImageLocked=true）等锁定态变化时必须调用本函数，
// 否则装扮/布景状态/故事版按钮不会随之置灰（refreshReferenceImageActionState 只覆盖参考图库）。
function refreshAllReferenceActionStates() {
    refreshReferenceImageActionState();
    refreshVariantAssetsActionState();
    refreshStoryboardActionState();
}

// 初始化
async function init() {
    // 永久关闭旧的全屏/整页遮罩，统一改为右侧底部状态栏
    const fullscreen = document.getElementById('fullscreenGenerating');
    if (fullscreen) fullscreen.remove();
    if (loadingOverlay) loadingOverlay.remove();
    
    // 强制移除任何可能存在的黑色半透明遮罩层
    document.querySelectorAll('.loading-overlay, .fullscreen-generating, [class*="overlay"], [class*="mask"], [class*="backdrop"]').forEach(el => {
        if (el && el.style) {
            el.style.display = 'none';
            el.remove();
        }
    });

    await Promise.all([initI18n(), loadFrontendConfig()]);
    initCustomSelects();
    renderWelcomeMessage(true);
    updateProjectActionState();
    connectWebSocket();
    bindEvents();
    // 刷新恢复：若本 Tab 仍有未结束的项目，从后端快照恢复 UI 与进度并继续。
    await restoreProjectOnLoad();
}

// 页面加载（含刷新）时尝试恢复正在运行的项目。
// - 依据 sessionStorage 中的 active_project_id（仅在真实项目创建后写入）。
// - 通过后端 /project/{id}/restore 拉取快照；后端内存未命中会回源 TOS，
//   因此本地与云端多实例刷新后都能恢复同一项目，实现「继续执行而非新建」。
async function restoreProjectOnLoad() {
    const projectId = getPersistedProjectId();
    if (!projectId) return;

    try {
        const response = await fetch(`/project/${encodeURIComponent(projectId)}/restore`);
        if (!response.ok) {
            // 404 等：项目已不存在（已结束/被清理），清除标记，正常新开局面。
            persistActiveProject(null);
            return;
        }
        const result = await parseJsonResponse(response);
        if (!result || !result.success || !result.snapshot) {
            persistActiveProject(null);
            return;
        }

        const snap = result.snapshot;
        if (snap.is_ended) {
            // 项目已结束：不恢复，清除标记。
            persistActiveProject(null);
            return;
        }

        applyRestoredSnapshot(snap);
    } catch (e) {
        console.error('Restore project on load failed:', e);
        // 恢复失败不阻塞正常使用：保留标记，用户可继续或手动结束。
    }
}

// 依据后端快照重建 UI 状态。尽量复用既有渲染函数，保证与实时链路一致。
function applyRestoredSnapshot(snap) {
    currentProjectId = snap.project_id;
    hasDraftProject = false;
    projectEnded = false;
    projectEndBeaconSent = false;
    persistActiveProject(currentProjectId);

    // 恢复后默认不自动推进（isAutoRunMode 保持 false），避免刷新即触发合成等破坏性动作；
    // 用户可再次点击继续或输入 auto 指令恢复全自动。视频模式选择器按快照回填。
    if (snap.video_generation_mode) {
        const videoModeSelect = document.getElementById('videoModeSelect');
        if (videoModeSelect) {
            videoModeSelect.value = snap.video_generation_mode === 'extend' ? 'extend' : 'parallel';
            if (typeof syncSelectDisplay === 'function') syncSelectDisplay('videoModeSelect');
        }
    }

    hideEmptyState();
    addAgentMessage(t('messages.projectRestored'));

    // 1) 剧本
    if (snap.script) {
        displayScript(snap.script);
        stepProgress.script = 100;
        // 恢复态下没有实时 step_complete 推送，需显式点亮剧本步骤（进度条 1 变绿）。
        updateStepHighlight('script_agent', 100);
    }

    // 2) 参考图库（含装扮/布景状态/故事版）
    if (snap.reference_output) {
        // 恢复态下不希望再自动触发确认倒计时，这里标记为已确认完成。
        const refOutput = { ...snap.reference_output, ready_for_confirmation: false };
        displayReferenceImage(refOutput);
        referenceImageLocked = true;
        stepProgress.reference_image = 100;
    }

    if (snap.comic_pdf_url || snap.comic_pdf_status === 'generating' || snap.comic_pdf_status === 'failed') {
        displayComicPdfLink({
            status: snap.comic_pdf_status || (snap.comic_pdf_url ? 'completed' : 'pending'),
            comic_pdf_url: snap.comic_pdf_url || '',
            error: snap.comic_pdf_error || ''
        });
    }

    // 3) 视频分镜
    const videos = snap.videos || [];
    if (videos.length > 0 || (snap.total_scenes && snap.current_step && String(snap.current_step).startsWith('videos'))) {
        currentStep = 'videos';
        referenceImageLocked = true;
        if (snap.total_scenes) videoTotalScenes = snap.total_scenes;
        ensureVideosContainer();
        videos.forEach((v) => {
            const sceneNum = v.scene_number;
            ensureVideoItem(sceneNum);
            if (v.url) {
                setVideoItemUrl(sceneNum, v.url);
                clearVideoItemLoading(sceneNum);
                markVideoGenerated(sceneNum);
            }
            if (v.completed) {
                markVideoReviewed(sceneNum, !!v.approved);
                const reviewEl = ensureReviewEl(sceneNum);
                if (reviewEl) {
                    if (v.accepted_over_retry) {
                        reviewEl.style.background = '#fff7e6';
                        reviewEl.style.border = '1px solid #ffd591';
                        reviewEl.style.color = '#fa8c16';
                        reviewEl.className = 'video-review-status is-accepted-over-retry';
                        reviewEl.innerHTML = `
                            <div class="review-status-title">${t('labels.acceptedOverRetry')}</div>
                            ${v.score >= 0 ? `<div class="review-status-text">${t('labels.score', { score: v.score })}</div>` : ''}
                        `;
                    } else if (v.approved) {
                        reviewEl.style.background = '#f6ffed';
                        reviewEl.style.border = '1px solid #b7eb8f';
                        reviewEl.style.color = '#52c41a';
                        reviewEl.className = 'video-review-status is-passed';
                        reviewEl.innerHTML = `
                            <div class="review-status-title">${t('labels.reviewPassed')}</div>
                            ${v.score >= 0 ? `<div class="review-status-text">${t('labels.score', { score: v.score })}</div>` : ''}
                        `;
                    } else {
                        reviewEl.className = 'video-review-status';
                        reviewEl.innerHTML = `<div class="review-status-title">${t('labels.waitingGenerateReview')}</div>`;
                    }
                }
            }
        });
        updateVideoStepProgressUI();
    }

    // 4) 最终合成视频
    if (snap.final_video_url) {
        displayFinalVideo(snap.final_video_url);
        currentStep = 'merge';
    } else if (snap.total_scenes > 0 && (snap.next_scene_index >= snap.total_scenes) && !snap.merge_blocked) {
        // 所有分镜已完成且未被重新生成阻塞：恢复到「等待/可进入合成」态。
        // 不自动开始倒计时，交由后端 step_complete 或用户继续驱动，避免重复合成。
        isWaitingForConfirm = true;
        pendingStep = 'merge';
        currentStep = 'videos';
    }

    updateOverallProgress();
    updateProjectActionState();
}

// 健壮解析后端 JSON 响应。
// 云端 API 网关在实例繁忙/冷启动时可能返回明文错误（如 "upstream request timeout"），
// 直接 response.json() 会抛 SyntaxError，前端表现为“发送失败/请重试”。
// 这里对非 JSON 响应给出可读错误，交由调用方决定是否重试或提示。
async function parseJsonResponse(response) {
    const rawText = await response.text();
    try {
        return JSON.parse(rawText);
    } catch (e) {
        throw new Error(`gateway_error: HTTP ${response.status} ${String(rawText).slice(0, 100)}`);
    }
}

// 获取/生成稳定的 client_id。
// 云端 API 网关会在长任务的静默期主动断开长连接 WebSocket，浏览器随后自动重连。
// 若每次重连都拿到新的 client_id，正在后台运行的任务仍向“旧 client_id”推送消息，
// 而旧连接已 close，导致进度/完成消息丢失（UI 卡住）、并触发误清理（合成时“未找到项目”）。
// 这里用 sessionStorage 持久化一个稳定的 client_id，重连时携带，让后端复用同一连接标识。
function getStableClientId() {
    try {
        let cid = sessionStorage.getItem('ws_client_id');
        if (!cid) {
            cid = (window.crypto && window.crypto.randomUUID)
                ? window.crypto.randomUUID()
                : 'cid-' + Date.now() + '-' + Math.random().toString(16).slice(2);
            sessionStorage.setItem('ws_client_id', cid);
        }
        return cid;
    } catch (e) {
        // sessionStorage 不可用时退化为内存值（至少保证单次会话内稳定）。
        if (!wsClientId) {
            wsClientId = 'cid-' + Date.now() + '-' + Math.random().toString(16).slice(2);
        }
        return wsClientId;
    }
}

// 连接 WebSocket - 添加3600秒长链接和心跳机制
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // 携带稳定 client_id，保证网关断连重连后仍复用同一连接标识。
    const stableClientId = getStableClientId();
    wsClientId = stableClientId;
    ws = new WebSocket(`${protocol}//${window.location.host}/ws?client_id=${encodeURIComponent(stableClientId)}`);
    
    ws.onopen = () => {
        // 启动心跳机制
        startHeartbeat();
    };
    
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleWebSocketMessage(data);
    };
    
    ws.onclose = () => {
        stopHeartbeat();
        setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };
}

// 启动心跳 - 每30秒发送一次ping，保持3600秒长链接
function startHeartbeat() {
    // 每30秒发送一次心跳
    heartbeatInterval = setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
        }
    }, 30000); // 30秒
}

// 停止心跳
function stopHeartbeat() {
    if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
        heartbeatInterval = null;
    }
}

// 处理 WebSocket 消息
function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'connection':
            // 后端下发的 client_id，用于后续 HTTP 请求把任务绑定到当前页面的 WS
            wsClientId = data.data?.client_id || null;
            break;
        case 'chat_response':
            addAgentMessage(data.data.message);
            if (data.data.project_id) {
                currentProjectId = data.data.project_id;
                hasDraftProject = false; // 后端已确认真实项目
                projectEnded = false;
                projectEndBeaconSent = false;
                persistActiveProject(currentProjectId);
                updateProjectActionState();
            }
            break;
            
        case 'progress':
            // 根据消息内容判断步骤类型
            let stepType = null;
            if (data.data.agent === 'image_agent') {
                // 根据进度判断是参考图还是分镜图
                if (data.data.progress <= 35) {
                    stepType = 'reference';  // 参考图阶段
                } else {
                    stepType = 'scenes';     // 分镜图阶段
                }
            }
            updateStepProgress(data.data.agent, data.data.progress, stepType);
            updateOverallProgress();
            updateStepHighlight(data.data.agent, data.data.progress, stepType);
            break;
            
        case 'agent_output':
            hideEmptyState();
            hideFullscreenGenerating();
            handleAgentOutput(data.data.agent, data.data.output);
            break;

        case 'reference_asset_regenerated':
            handleReferenceAssetRegenerated(data.data);
            break;

        case 'video_scene_regenerated':
            handleVideoSceneRegenerated(data.data);
            break;

        case 'step_complete':
            hideFullscreenGenerating();
            // 防止重复处理相同的 step_complete 消息（含 reference_stage，避免各子阶段 message 相同被误去重）
            const stepKey = `${data.data.step}_${data.data.reference_stage || ''}_${data.data.message}`;
            if (window.lastStepComplete === stepKey) {
                break;
            }
            window.lastStepComplete = stepKey;
            // 参考图子阶段完成走独立分发（不复用主步骤 handleStepComplete）
            if (data.data.step === 'reference_image' && data.data.reference_stage) {
                handleReferenceStageComplete(data.data.reference_stage, data.data);
                break;
            }
            handleStepComplete(data.data.step, data.data.message);
            break;

        case 'status':
            // 根据当前步骤显示全屏转转效果
            const stepNames = {
                'script_agent': t('steps.scriptTitle'),
                'image_agent': t('steps.imageAgent'),
                'video_agent': t('steps.videosTitle'),
                'video_review_agent': t('steps.videoReview'),
                'merge_agent': t('steps.mergeTitle')
            };
            const agentName = stepNames[data.data.agent] || t('steps.processing');
            showFullscreenGenerating(data.data.message, agentName);
            break;

        case 'error':
            hideFullscreenGenerating();
            hideLoading();
            addAgentMessage(data.data.message);
            break;
            
        case 'pong':
            break;
    }
}

// 隐藏placeholder
function hideEmptyState() {
    if (emptyState) {
        emptyState.style.display = 'none';
    }
}

// 显示步骤状态到右侧底部状态栏（不再遮罩整个页面）
function showFullscreenGenerating(message, stepName) {
    renderStatusBar(message || t('status.generating'), 'loading', stepName || '');
}

// 隐藏步骤状态栏中的“加载态”
function hideFullscreenGenerating() {
    if (statusSection && statusSection.dataset.mode === 'loading') {
        statusSection.style.display = 'none';
        statusSection.dataset.mode = '';
        statusSection.innerHTML = `<p class="status-text" id="statusText">${t('progress.waiting')}</p>`;
    }
}

// 显示生成中指示器（转转效果）- 仅在预览区域显示，不用于图片生成
function showGeneratingIndicator() {
    // 图片生成时不显示顶部转转效果，改用全屏遮罩
    // 此函数保留用于其他用途
}

// 隐藏生成中指示器
function hideGeneratingIndicator() {
    // 此函数保留用于兼容性
}

// 更新步骤进度
function updateStepProgress(agent, progress, stepType = null) {
    // agent 到步骤的映射
    const stepMap = {
        'script_agent': 'script',
        'image_agent': stepType || 'reference_image',  // 根据 stepType 区分参考图和分镜图
        'video_agent': 'videos',
        'video_review_agent': 'videos',  // 视频审核也算在视频步骤进度中
        'merge_agent': 'merge'
    };

    const stepName = stepMap[agent];
    if (stepName) {
        stepProgress[stepName] = progress;
    }
}

// 更新整体进度
function updateOverallProgress() {
    // 计算整体进度：每个步骤占25%（4个步骤）
    const stepWeights = {
        script: 0.25,           // 剧本：25%
        reference_image: 0.25,  // 参考图：25%
        videos: 0.25,           // 视频：25%
        merge: 0.25             // 合成：25%
    };

    let totalProgress = 0;
    for (const [step, progress] of Object.entries(stepProgress)) {
        if (stepWeights[step]) {
            totalProgress += (progress * stepWeights[step]);
        }
    }

    overallProgress = Math.round(totalProgress);
    // 合成完成时，强制收尾到 100%
    if (stepProgress.script >= 100 && stepProgress.reference_image >= 100 && stepProgress.videos >= 100 && stepProgress.merge >= 100) {
        overallProgress = 100;
    }

    // 更新UI
    const progressFill = document.getElementById('overallProgressFill');
    const progressPercent = document.getElementById('overallProgressPercent');

    if (progressFill) {
        progressFill.style.width = `${overallProgress}%`;
    }
    if (progressPercent) {
        progressPercent.textContent = `${overallProgress}%`;
    }
}

// 更新步骤高亮
function updateStepHighlight(agent, progress, stepType = null) {
    // agent 到步骤的映射，支持5个步骤
    const stepMap = {
        'script_agent': 'script',
        'image_agent': stepType || 'reference',  // 根据 stepType 区分参考图和分镜图
        'video_agent': 'videos',
        'video_review_agent': 'videos',  // 视频审核也算在视频步骤中
        'merge_agent': 'merge'
    };

    const stepName = stepMap[agent];
    if (!stepName) return;

    const stepElement = document.getElementById(`step-${stepName}`);
    if (!stepElement) return;

    // 移除所有状态类
    stepElement.classList.remove('active', 'completed');

    // 根据进度设置状态
    if (progress >= 100) {
        stepElement.classList.add('completed');
    } else if (progress > 0) {
        stepElement.classList.add('active');
    }
}

// 绑定事件
function bindEvents() {
    if (languageSelect) {
        languageSelect.addEventListener('change', async (event) => {
            await setLanguage(event.target.value);
        });
    }

    // 发送消息
    sendBtn.addEventListener('click', sendMessage);
    textInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });

    // 图片上传
    imageBtn.addEventListener('click', () => imageInput.click());
    imageInput.addEventListener('change', handleImageUpload);

    if (endProjectBtn) {
        endProjectBtn.addEventListener('click', () => {
            void endProject('user_end');
        });
    }

    // 录音 - 按一下开始，再按一下停止
    micBtn.addEventListener('click', toggleRecording);

    // 媒体弹窗关闭
    mediaModalClose.addEventListener('click', closeMediaModal);
    mediaModal.addEventListener('click', (e) => {
        if (e.target === mediaModal) closeMediaModal();
    });

    // ESC键关闭弹窗
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && mediaModal.classList.contains('active')) {
            closeMediaModal();
        }
    });

    // 注意：不再在 pagehide/beforeunload 上自动结束项目。
    // 浏览器刷新同样会触发这些事件，若在此结束项目，刷新后进度会丢失。
    // 结束项目现在只能由用户显式点击「结束项目」按钮触发（见 endProjectBtn）。
    // 刷新恢复依赖 sessionStorage 中的 active_project_id + 后端 /restore 快照。
}

// 打开媒体弹窗（用于图片）
function openMediaModal(type, src) {
    mediaModalBody.innerHTML = '';

    if (type === 'image') {
        const img = document.createElement('img');
        img.src = src;
        img.alt = t('labels.zoomedImageAlt');
        img.style.maxWidth = '90vw';
        img.style.maxHeight = '90vh';
        img.style.objectFit = 'contain';
        mediaModalBody.appendChild(img);

        mediaModal.classList.add('active');
        document.body.style.overflow = 'hidden'; // 防止背景滚动
    } else if (type === 'video') {
        // 视频使用专门的 openVideoModal 函数
        openVideoModal(src);
    }
}

// 切换录音状态
async function toggleRecording() {
    if (isRecording) {
        stopRecording();
    } else {
        await startRecording();
    }
}

const START_COMMAND_KEYWORDS = [
    '开始生成', '开始', '生成', '启动', '创建', '开始制作', '开始创作', '执行',
    '開始生成', '開始', '生成', '啟動', '建立', '開始製作', '開始創作', '執行',
    'start', 'generate', 'begin', 'create', 'launch', 'run',
    '開始する', '始める', 'スタート', '生成開始', '実行',
    'iniciar', 'inicia', 'empezar', 'empieza', 'comenzar', 'comienza', 'generar', 'crear', 'ejecutar'
];

const CONTINUE_COMMAND_KEYWORDS = [
    '继续', '下一步', '确认', '确认继续', '进行下一步', '好', '好的', '行', '可以', '没问题', '确认并继续',
    '繼續', '下一步', '確認', '確認繼續', '進行下一步', '好', '好的', '行', '可以', '沒問題', '確認並繼續',
    'continue', 'next', 'go', 'ok', 'okay', 'yes',
    '続ける', '次へ', '次に進む', '続行', '確認', 'はい', 'いいよ', 'オーケー',
    'continuar', 'continua', 'siguiente', 'seguir', 'vale', 'bueno', 'sí', 'si', 'ok'
];

const REGENERATE_COMMAND_KEYWORDS = [
    '重新生成', '重做', '重新制作', '再来一次', '重来',
    '重新生成', '重做', '重新製作', '再來一次', '重來',
    'regenerate', 'redo', 'regen', 'restart',
    '再生成', 'やり直し', 'リジェン',
    'regenerar', 'rehacer', 'regen', 'otra vez'
];

const ROLLBACK_COMMAND_KEYWORDS = [
    '退回', '回退',
    '退回到', '回退到',
    'rollback', 'roll back',
    'go back', 'back to',
    'ロールバック', '戻る', '前に戻る',
    'retroceder', 'volver', 'volver a'
];

const AUTO_RUN_COMMAND_KEYWORDS = [
    '全自动', '一键生成', 'autorun', 'auto', '自动', '全自动生成', '一键制作', '自动运行',
    '全自動', '一鍵生成', '自動', '全自動生成', '一鍵製作', '自動運行',
    '自動', '自動生成', '自動モード', 'オート', 'autorun',
    'auto', 'autorun', 'automatic', 'automatic mode',
    'automático', 'automatico', 'modo automático', 'modo automatico', 'auto'
];

function normalizeCommandMessage(message) {
    return (message || '')
        .trim()
        .toLowerCase()
        .normalize('NFKC')
        .replace(/[“”"'`]/g, '')
        .replace(/[，。！？、；：,.!?;:()[\]{}]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function matchesCommandKeyword(message, keywords) {
    const normalized = normalizeCommandMessage(message);
    if (!normalized) return false;
    return keywords.some((keyword) => {
        const normalizedKeyword = normalizeCommandMessage(keyword);
        return normalized === normalizedKeyword || normalized.startsWith(`${normalizedKeyword} `);
    });
}

function containsCommandKeyword(message, keywords) {
    const normalized = normalizeCommandMessage(message);
    if (!normalized) return false;
    return keywords.some((keyword) => {
        const normalizedKeyword = normalizeCommandMessage(keyword);
        return (
            normalized === normalizedKeyword ||
            normalized.startsWith(`${normalizedKeyword} `) ||
            normalized.includes(` ${normalizedKeyword} `) ||
            normalized.endsWith(` ${normalizedKeyword}`) ||
            normalized.endsWith(normalizedKeyword)
        );
    });
}

// 检查消息是否匹配任何开始生成指令
function isStartCommand(message) {
    return matchesCommandKeyword(message, START_COMMAND_KEYWORDS) || matchesCommandKeyword(message, CONTINUE_COMMAND_KEYWORDS);
}

// 检查消息是否匹配任何继续指令
function isContinueCommand(message) {
    return matchesCommandKeyword(message, CONTINUE_COMMAND_KEYWORDS);
}

// 检查消息是否匹配任何重新生成指令
function isRegenerateCommand(message) {
    return matchesCommandKeyword(message, REGENERATE_COMMAND_KEYWORDS);
}

function parseRegenerateStep(message) {
    let normalized = normalizeCommandMessage(message);
    for (const keyword of REGENERATE_COMMAND_KEYWORDS) {
        const normalizedKeyword = normalizeCommandMessage(keyword);
        if (normalized === normalizedKeyword) {
            return '';
        }
        if (normalized.startsWith(`${normalizedKeyword} `)) {
            normalized = normalized.slice(normalizedKeyword.length).trim();
            break;
        }
        if (normalized.startsWith(`${normalizedKeyword}-`)) {
            normalized = normalized.slice(normalizedKeyword.length + 1).trim();
            break;
        }
    }
    return normalized.replace(/^[-\s]+/, '').trim();
}

// 检查消息是否是退回步骤指令
function isRollbackCommand(message) {
    const normalized = normalizeCommandMessage(message);
    return ROLLBACK_COMMAND_KEYWORDS.some((keyword) => {
        const normalizedKeyword = normalizeCommandMessage(keyword);
        return normalized === normalizedKeyword || normalized.startsWith(`${normalizedKeyword} `) || normalized.startsWith(`${normalizedKeyword}-`);
    });
}

// 解析退回步骤指令
function parseRollbackStep(message) {
    let normalized = normalizeCommandMessage(message);
    for (const keyword of ROLLBACK_COMMAND_KEYWORDS) {
        const normalizedKeyword = normalizeCommandMessage(keyword);
        if (normalized === normalizedKeyword) {
            return '';
        }
        if (normalized.startsWith(`${normalizedKeyword} `)) {
            normalized = normalized.slice(normalizedKeyword.length).trim();
            break;
        }
        if (normalized.startsWith(`${normalizedKeyword}-`)) {
            normalized = normalized.slice(normalizedKeyword.length + 1).trim();
            break;
        }
    }

    normalized = normalized
        .replace(/^[-\s]+/, '')
        .replace(/^(to|a|至|到)\s+/, '')
        .trim();
    return normalized;
}

// 获取步骤映射（多语言步骤名到内部步骤ID）
function getStepMapping() {
    return {
        '剧本': 'script',
        '腳本': 'script',
        'script': 'script',
        'screenplay': 'script',
        'storyboard script': 'script',
        'guion': 'script',
        'guión': 'script',
        '脚本': 'script',
        '台本': 'script',
        '参考图': 'reference_image',
        '參考圖': 'reference_image',
        'reference': 'reference_image',
        'reference image': 'reference_image',
        'reference images': 'reference_image',
        'imagen de referencia': 'reference_image',
        '参照画像': 'reference_image',
        '参考画像': 'reference_image',
        'ref image': 'reference_image',
        '分镜视频': 'videos',
        '分鏡視頻': 'videos',
        '分镜影片': 'videos',
        '分鏡影片': 'videos',
        'storyboard video': 'videos',
        'storyboard videos': 'videos',
        'scene video': 'videos',
        'scene videos': 'videos',
        'video del storyboard': 'videos',
        'videos del storyboard': 'videos',
        '视频': 'videos',
        '影片': 'videos',
        'video': 'videos',
        'videos': 'videos',
        '動画': 'videos',
        'ビデオ': 'videos',
        '合成': 'merge',
        '合成影片': 'merge',
        'merge': 'merge',
        'final merge': 'merge',
        'merged video': 'merge',
        'union': 'merge',
        'unión': 'merge',
        'mezcla': 'merge',
        '結合': 'merge',
        'マージ': 'merge',
        '合并': 'merge'
    };
}

// 获取步骤顺序
function getStepOrder() {
    return ['script', 'reference_image', 'videos', 'merge'];
}

// 验证退回步骤是否合法
function validateRollbackStep(targetStepName) {
    const stepMapping = getStepMapping();
    const targetStep = stepMapping[targetStepName];

    if (!targetStep) {
        return {
            valid: false,
            error: t('messages.rollbackInvalidStep', { step: targetStepName })
        };
    }

    const stepOrder = getStepOrder();
    const currentStepIndex = stepOrder.indexOf(currentStep);
    const targetStepIndex = stepOrder.indexOf(targetStep);

    // 如果当前没有步骤（初始状态），不能退回
    if (!currentStep || currentStep === 'init') {
        return {
            valid: false,
            error: t('messages.rollbackNoStartedStep')
        };
    }

    // 检查目标步骤是否在已完成步骤中
    if (targetStepIndex > currentStepIndex) {
        const completedSteps = stepOrder.slice(0, currentStepIndex + 1).map(s => {
            const names = Object.entries(stepMapping).find(([, v]) => v === s);
            return names ? names[0] : s;
        });
        return {
            valid: false,
            error: t('messages.rollbackStepNotCompleted', {
                step: targetStepName,
                completedSteps: completedSteps.join(' → ')
            })
        };
    }

    return {
        valid: true,
        targetStep: targetStep,
        targetStepName: targetStepName
    };
}

// 检查消息是否匹配全自动模式指令
function isAutoRunCommand(message) {
    return matchesCommandKeyword(message, AUTO_RUN_COMMAND_KEYWORDS);
}

function containsAutoRunHint(message) {
    return containsCommandKeyword(message, AUTO_RUN_COMMAND_KEYWORDS);
}

// 发送消息
async function sendMessage() {
    const message = textInput.value.trim();
    if (!message && uploadedImages.length === 0 && !uploadedAudio) {
        return;
    }

    if (uploadedImages.length > 0 && !validateUploadedImageNames()) {
        return;
    }

    // 判定是否应从提示语触发全自动：只要后端尚未创建真实项目即可（无项目 ID，
    // 或仅有上传图片/音频生成的草稿 ID）。不能用 !currentProjectId——上传会提前
    // 生成草稿 ID 使其变真值，从而漏判，导致上传图片时 auto 提示无法进入全自动。
    const hasRealProject = !!currentProjectId && !hasDraftProject;
    const shouldEnableAutoRunFromPrompt = !hasRealProject && containsAutoRunHint(message);

    if (uploadedImages.length > 0) {
        ensureDraftProjectId();
    }
    if (uploadedAudio) {
        ensureDraftProjectId();
    }
    
    // 检查是否是"继续"指令（支持多种说法）
    if (isContinueCommand(message) && isWaitingForConfirm && pendingReferenceStage && pendingReferenceStage !== 'videos') {
        // 参考图子阶段等待中（category1/category2 完成后）：推进下一子阶段。
        textInput.value = '';
        addUserMessage(message);
        isWaitingForConfirm = false;
        cancelAutoRunCountdown();
        hideStatusSection();
        proceedToReferenceStage(pendingReferenceStage);
        return;
    }

    // 检查是否是"继续"指令（支持多种说法）
    if (isContinueCommand(message) && isWaitingForConfirm && pendingStep) {
        textInput.value = '';
        addUserMessage(message);
        isWaitingForConfirm = false;
        hideStatusSection();

        // 特殊处理：参考图完成后下一步是videos，应该调用新流程
        if (currentStep === 'reference_image' && pendingStep === 'videos') {
            startVideoGenerationAfterReference();
        } else {
            // 直接调用 startStep 执行下一步
            startStep(pendingStep);
        }
        return;
    }
    
    // 检查是否是"重新生成"指令（支持多种说法）
    if (isRegenerateCommand(message) && isWaitingForConfirm && currentStep) {
        textInput.value = '';
        addUserMessage(message);
        isWaitingForConfirm = false;
        hideStatusSection();
        startStep(currentStep);
        return;
    }

    if (isRegenerateCommand(message) && currentStep) {
        const targetStepName = parseRegenerateStep(message);
        if (targetStepName) {
            textInput.value = '';
            addUserMessage(message);
            const validation = validateRollbackStep(targetStepName);
            if (!validation.valid) {
                addAgentMessage(validation.error);
                return;
            }
            const { targetStep, targetStepName: stepName } = validation;
            addAgentMessage(t('messages.rollbackStarting', { step: stepName }));
            sendRollbackRequest(targetStep);
            return;
        }
    }

    // 检查是否是退回步骤指令
    if (isRollbackCommand(message)) {
        textInput.value = '';
        addUserMessage(message);

        const targetStepName = parseRollbackStep(message);
        const validation = validateRollbackStep(targetStepName);

        if (!validation.valid) {
            addAgentMessage(validation.error);
            return;
        }

        // 验证通过，执行退回
        const { targetStep, targetStepName: stepName } = validation;

        addAgentMessage(t('messages.rollbackStarting', { step: stepName }));

        // 发送退回请求到后端
        sendRollbackRequest(targetStep);
        return;
    }

    // 检查是否是"开始生成"指令（支持多种说法）
    // 如果已经有项目ID且风格信息已收集，直接开始生成
    if (isStartCommand(message) && currentProjectId) {
        textInput.value = '';
        addUserMessage(message);
        // 开始第一步：生成剧本
        isWaitingForConfirm = false;
        startStep('script');
        return;
    }

    // 检查是否是全自动模式指令
    if (isAutoRunCommand(message)) {
        textInput.value = '';
        addUserMessage(message);
        addAgentMessage(t('messages.enterAutoRun'));

        // 启用全自动模式标志
        isAutoRunMode = true;

        // 处于参考图子阶段等待时（category1/category2 完成后）：开启子阶段倒计时后返回。
        if (isWaitingForConfirm && pendingReferenceStage && pendingReferenceStage !== 'videos') {
            maybeStartReferenceStageCountdown();
            return;
        }

        // 确定从哪个步骤开始
        let startStepName = 'script';
        if (isWaitingForConfirm && pendingStep) {
            // 处于“等待继续”状态时，必须优先从 pendingStep 继续，
            // 不能按 currentStep 推导下一步，否则 videos -> merge 会误跳过剩余分镜。
            startStepName = pendingStep;
        } else if (currentStep) {
            // 如果当前有步骤，从下一步开始
            const steps = ['script', 'reference_image', 'videos', 'merge'];
            const currentIndex = steps.indexOf(currentStep);
            if (currentIndex >= 0 && currentIndex < steps.length - 1) {
                startStepName = steps[currentIndex + 1];
            } else if (currentIndex === steps.length - 1) {
                addAgentMessage(t('messages.allStepsCompleted'));
                isAutoRunMode = false;
                return;
            }
        }

        // 如果没有项目ID，需要先创建项目
        if (!currentProjectId) {
            if (uploadedImages.length > 0) {
                ensureDraftProjectId();
            }
            // 先发送消息创建项目
            showLoading(t('status.processing'));
            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: new URLSearchParams({
                        message: message,
                        project_id: currentProjectId || '',
                        client_id: wsClientId || '',
                        image_urls: JSON.stringify(getUploadedImageUrls()),
                        image_assets: JSON.stringify(getUploadedImageAssets()),
                        audio_url: '',
                        ui_language: currentLanguage,
                        use_original_reference: useOriginalReference ? 'true' : 'false'
                    })
                });
                const result = await parseJsonResponse(response);
                if (result.success) {
                    currentProjectId = result.project_id;
                    hasDraftProject = false; // 后端已创建真实项目
                    projectEnded = false;
                    projectEndBeaconSent = false;
                    persistActiveProject(currentProjectId);
                    updateProjectActionState();
                    addAgentMessage(result.response);
                } else {
                    addAgentMessage(t('messages.serverError', { error: result.error }));
                    isAutoRunMode = false;
                    hideLoading();
                    return;
                }
            } catch (error) {
                console.error('Create project error:', error);
                addAgentMessage(t('messages.createProjectFailedRetry'));
                isAutoRunMode = false;
                hideLoading();
                return;
            }
            hideLoading();
        }

        isWaitingForConfirm = false;
        hideStatusSection();

        // 延迟后开始执行
        setTimeout(() => {
            startStep(startStepName);
        }, 1500);
        return;
    }
    
    // 添加用户消息到聊天区
    addUserMessage(message, uploadedImages, uploadedAudio);
    
    // 清空输入
    textInput.value = '';
    
    // 显示加载
    showLoading(t('status.processing'));
    
    try {
        if (shouldEnableAutoRunFromPrompt) {
            isAutoRunMode = true;
        }

        // 先上传音频（如果有）
        let audioUrl = null;
        if (uploadedAudio) {
            audioUrl = await uploadFile(uploadedAudio, currentProjectId || ensureDraftProjectId());
        }
        
        // 发送消息
        const response = await fetch('/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                message: message,
                project_id: currentProjectId || '',
                client_id: wsClientId || '',
                image_urls: JSON.stringify(getUploadedImageUrls()),
                image_assets: JSON.stringify(getUploadedImageAssets()),
                audio_url: audioUrl || '',
                ui_language: currentLanguage,
                use_original_reference: useOriginalReference ? 'true' : 'false'
            })
        });
        
        const result = await parseJsonResponse(response);
        
        if (result.success) {
            currentProjectId = result.project_id;
            hasDraftProject = false; // 后端已创建真实项目
            projectEnded = false;
            projectEndBeaconSent = false;
            persistActiveProject(currentProjectId);
            updateProjectActionState();
            addAgentMessage(result.response);

            if (result.script_updated && result.script_output) {
                displayScript(result.script_output);
                currentStep = 'script';
                handleStepComplete('script');
            }
            
            if (result.ready_for_script_start) {
                isWaitingForConfirm = false;
                pendingStep = null;
                hideStatusSection();
                setTimeout(() => {
                    startStep('script');
                }, 100);
            }
        } else {
            if (shouldEnableAutoRunFromPrompt) {
                isAutoRunMode = false;
            }
            addAgentMessage(t('messages.serverError', { error: result.error }));
        }
        
    } catch (error) {
        console.error('Send message error:', error);
        if (shouldEnableAutoRunFromPrompt) {
            isAutoRunMode = false;
        }
        addAgentMessage(t('messages.sendFailedRetry'));
    }
    
    hideLoading();
    
    // 清空已上传文件
    uploadedImages = [];
    uploadedAudio = null;
    useOriginalReference = false;
    updateUploadedFiles();
}

// 发送退回步骤请求
async function sendRollbackRequest(targetStep) {
    if (!currentProjectId) {
        addAgentMessage(t('messages.createProjectFirst'));
        return;
    }

    showLoading(t('status.rollingBack'));

    try {
        const response = await fetch('/rollback', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                project_id: currentProjectId,
                client_id: wsClientId || '',
                target_step: targetStep,
                ui_language: currentLanguage
            })
        });

        let result = null;
        const responseText = await response.text();
        try {
            result = responseText ? JSON.parse(responseText) : {};
        } catch (parseError) {
            throw new Error(responseText || getHttpErrorMessage(response.status));
        }

        if (response.ok && result.success) {
            // 更新当前步骤
            currentStep = targetStep;
            isWaitingForConfirm = false;
            pendingStep = null;
            lastFinalVideoUrl = null;
            mergeStepVisible = false;
            referenceImageLocked = false;
            referenceImageRegenerating = false;
            cancelAutoRunCountdown();
            hideFullscreenGenerating();
            hideStatusSection();

            // 清空预览区域中该步骤及之后的所有输出
            clearPreviewFromStep(targetStep);

            if (targetStep === 'reference_image') {
                lastReferenceImageOutput = null;
                lastImagesOutput = null;
                videoOutputsByScene = {};
                videoReviewOutputsByScene = {};
                videoSceneState = {};
                videoTotalScenes = null;
                stepProgress.reference_image = 0;
                stepProgress.videos = 0;
                stepProgress.merge = 0;
                updateOverallProgress();
            }

            addAgentMessage(t('messages.rollbackSuccess', { step: getStepName(result.target_step) }));

            // 延迟后开始重新生成
            setTimeout(() => {
                startStep(targetStep);
            }, 1500);
        } else {
            addAgentMessage(t('messages.rollbackFailed', { error: (result && result.error) || `HTTP ${response.status}` }));
        }
    } catch (error) {
        console.error('Rollback error:', error);
        addAgentMessage(t('messages.rollbackFailed', { error: error.message || t('labels.unknown') }));
    }

    hideLoading();
}

// 清空预览区域从某步骤开始的所有输出
function clearPreviewFromStep(targetStep) {
    const stepOrder = ['script', 'reference_image', 'videos', 'merge'];
    const targetIndex = stepOrder.indexOf(targetStep);

    if (targetIndex === -1) return;

    // 清空该步骤及之后的所有预览内容
    const stepsToClear = stepOrder.slice(targetIndex);

    stepsToClear.forEach(step => {
        switch(step) {
            case 'script':
                document.getElementById('script-card')?.remove();
                break;
            case 'reference_image':
                document.getElementById('reference-image-card')?.remove();
                break;
            case 'videos':
                document.querySelectorAll('.video-item[id^="video-item-"]').forEach(item => item.remove());
                break;
            case 'merge':
                document.getElementById('merge-step-card')?.remove();
                document.getElementById('final-video-card')?.remove();
                break;
        }
    });
}

// 开始执行某一步骤
function startStep(step) {
    currentStep = step;

    if (!currentProjectId) {
        alert(t('messages.createProjectFirst'));
        return;
    }

    // 重置 step_complete 消息记录，允许重新生成时正常处理
    window.lastStepComplete = null;

    // 重新进入参考图步骤时，重置子阶段状态机（category1 将从头开始）。
    if (step === 'reference_image') {
        referenceStage = null;
        pendingReferenceStage = null;
        referenceStageHasCategory2 = false;
    }

    showLoading(getStepLoadingText(step));

    // 发送步骤执行请求
    ws.send(JSON.stringify({
        type: 'execute_step',
        project_id: currentProjectId,
        step: step,
        ui_language: currentLanguage,
        review_mode: getCurrentReviewMode(),
        generation_mode: getCurrentVideoMode()
    }));
}

function regenerateScriptFromCard() {
    if (!currentProjectId) {
        alert(t('messages.createProjectFirst'));
        return;
    }

    cancelAutoRunCountdown();
    hideStatusSection();
    isWaitingForConfirm = false;
    pendingStep = null;

    const hasDownstreamOutputs =
        currentStep === 'reference_image' ||
        currentStep === 'videos' ||
        currentStep === 'merge' ||
        !!lastReferenceImageOutput ||
        !!lastImagesOutput ||
        Object.keys(videoOutputsByScene).length > 0 ||
        !!lastFinalVideoUrl;

    if (hasDownstreamOutputs) {
        addAgentMessage(t('messages.rollbackStarting', { step: getStepName('script') }));
        sendRollbackRequest('script');
        return;
    }

    startStep('script');
}

// 获取步骤加载文本
function getStepLoadingText(step) {
    const texts = {
        'script': t('loading.script'),
        'reference_image': t('loading.referenceImage'),
        'videos': t('loading.videos'),
        'merge': t('loading.merge')
    };
    return texts[step] || t('status.processing');
}

// 处理步骤完成
function handleStepComplete(step) {
    hideLoading();

    currentStep = step;

    // 更新步骤进度为100%
    stepProgress[step] = 100;
    updateOverallProgress();

    // 计算下一步
    const nextStep = getNextStep(step);

    if (nextStep) {
        isWaitingForConfirm = true;
        pendingStep = nextStep;

        const shouldShowCountdown = isAutoRunMode || step === 'script' || step === 'reference_image' || step === 'videos';
        if (shouldShowCountdown && canStartPendingStepCountdown(step)) {
            showAutoRunCountdown(nextStep, step, isAutoRunMode);
            return;
        }

        if (!shouldShowCountdown) {
            showPendingStepPrompt(step, nextStep);
        }
    } else {
        // 所有步骤完成（merge 步骤）
        isWaitingForConfirm = false;
        pendingStep = null;
        isAutoRunMode = false; // 退出全自动模式
        hideStatusSection();
        addAgentMessage(t('messages.allVideosCompleted'));
    }
}

// 显示状态区域
function showStatusSection(text) {
    renderStatusBar(text, 'info');
}

// 隐藏状态区域
function hideStatusSection() {
    statusSection.style.display = 'none';
    statusSection.dataset.mode = '';
    statusSection.innerHTML = `<p class="status-text" id="statusText">${t('progress.waiting')}</p>`;
}

// 获取步骤名称
function getStepName(step) {
    const names = {
        'script': t('steps.scriptTitle'),
        'reference_image': t('steps.referenceImageTitle'),
        'videos': t('steps.videosTitle'),
        'merge': t('steps.mergeTitle')
    };
    return names[step] || step;
}

// 获取下一步
function getNextStep(currentStep) {
    const steps = ['script', 'reference_image', 'videos', 'merge'];
    const currentIndex = steps.indexOf(currentStep);
    if (currentIndex >= 0 && currentIndex < steps.length - 1) {
        return steps[currentIndex + 1];
    }
    return null;
}

// 显示步骤倒计时；autoProceed=false 时，倒计时结束后仅停在等待聊天指令状态
// 公共倒计时渲染：创建 #autoRunCountdownMessage / #countdownValue / #exitAutoRunBtn，
// 并按 1s 递减；归零后调用 onFinish()。主步骤与参考图子阶段共用，避免 DOM id 冲突。
function renderCountdownMessage(onFinish) {
    cancelAutoRunCountdown();
    const countdownSeconds = Math.max(0, Number(frontendConfig.auto_run_countdown_seconds) || 10);
    autoRunCountdownValue = countdownSeconds;

    const messageDiv = document.createElement('div');
    messageDiv.className = 'message agent-message auto-run-countdown';
    messageDiv.id = 'autoRunCountdownMessage';
    messageDiv.innerHTML = `
        <div class="message-avatar">&#129302;</div>
        <div class="message-content">
            <p style="margin-bottom: 10px;">${t('labels.autoRunNext')}</p>
            <p style="color: #1890ff; font-weight: bold; font-size: 16px; margin-bottom: 10px;">
                ${t('labels.countdown', { count: `<span id="countdownValue">${countdownSeconds}</span>` })}
            </p>
            <p style="font-size: 12px; color: #666; margin-bottom: 10px;">
                ${t('labels.autoRunExitTip')}
            </p>
            <button id="exitAutoRunBtn" style="
                background: #ff4d4f;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                cursor: pointer;
                font-size: 14px;
                display: flex;
                align-items: center;
                gap: 6px;
            ">
                <span>&#9940;</span> ${t('actions.exitAutoRun')}
            </button>
        </div>
    `;

    chatMessages.appendChild(messageDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    document.getElementById('exitAutoRunBtn').addEventListener('click', exitAutoRunMode);

    autoRunCountdown = setInterval(() => {
        autoRunCountdownValue--;
        const countdownEl = document.getElementById('countdownValue');
        if (countdownEl) {
            countdownEl.textContent = autoRunCountdownValue;
        }

        if (autoRunCountdownValue <= 0) {
            clearInterval(autoRunCountdown);
            autoRunCountdown = null;
            removeAutoRunCountdownMessage();
            onFinish();
        }
    }, 1000);
}

function showAutoRunCountdown(nextStep, completedStep, autoProceed = true) {
    renderCountdownMessage(() => {
        if (!autoProceed) {
            showPendingStepPrompt(completedStep, nextStep);
            return;
        }

        // 特殊处理：参考图完成后下一步是videos，应该调用新流程而不是旧流程
        if (completedStep === 'reference_image' && nextStep === 'videos') {
            addAgentMessage(t('messages.autoEnterNextNewFlow', { step: getStepName(nextStep) }));
            isWaitingForConfirm = false;
            hideStatusSection();
            startVideoGenerationAfterReference();
        } else {
            addAgentMessage(t('messages.autoEnterNext', { step: getStepName(nextStep) }));
            isWaitingForConfirm = false;
            hideStatusSection();
            startStep(nextStep);
        }
    });
}

// 退出全自动模式
function exitAutoRunMode() {
    // 清除倒计时器
    cancelAutoRunCountdown();

    // 设置状态
    isAutoRunMode = false;
    isWaitingForConfirm = true;

    // 显示退出提示
    addAgentMessage(t('messages.exitAutoRun'));
    showStatusSection(t('messages.exitAutoRunStatus'));
}

// ========== 参考图子阶段（category1/category2/category3）推进逻辑 ==========

// 依据当前完成阶段与是否存在分类2，计算下一目标：category2/category3/videos。
function computeNextReferenceStage(stage, hasCategory2) {
    if (stage === 'category1') {
        return hasCategory2 ? 'category2' : 'category3';
    }
    if (stage === 'category2') {
        return 'category3';
    }
    return 'videos';
}

// 子阶段就绪门控：要求本阶段资产齐全（stage_ready）且无正在进行的单张重生成。
function canStartReferenceStageCountdown(output) {
    return !!(output && output.stage_ready === true) && !hasPendingReferenceRegeneration();
}

// 手动模式下的子阶段等待提示（仿 showPendingStepPrompt）。
function showPendingReferenceStagePrompt(completedStage) {
    isWaitingForConfirm = true;
    const messageKey = {
        category1: 'messages.referenceCategory1Complete',
        category2: 'messages.referenceCategory2Complete',
        category3: 'messages.referenceCategory3Complete',
    }[completedStage] || 'messages.referenceComplete';
    addAgentMessage(t(messageKey));
}

// 参考图子阶段完成分发（不复用主步骤 getNextStep）。
function handleReferenceStageComplete(stage, output) {
    hideLoading();
    currentStep = 'reference_image';
    referenceStage = stage;
    referenceStageHasCategory2 = !!(output && output.has_category2);

    const nextStage = computeNextReferenceStage(stage, referenceStageHasCategory2);
    if (nextStage === 'videos') {
        // category3 已完成：复用主步骤 videos 等待/倒计时逻辑。
        pendingReferenceStage = 'videos';
        pendingStep = 'videos';
        isWaitingForConfirm = true;
        maybeStartPendingStepCountdown();
        return;
    }

    pendingReferenceStage = nextStage;
    isWaitingForConfirm = true;
    maybeStartReferenceStageCountdown();
}

// 依模式决定子阶段等待呈现：手动→提示，自动→倒计时（复用公共渲染）。
function maybeStartReferenceStageCountdown() {
    if (!pendingReferenceStage || pendingReferenceStage === 'videos') return;
    if (autoRunCountdown) return;
    if (!canStartReferenceStageCountdown(lastReferenceImageOutput)) return;

    if (isAutoRunMode) {
        showReferenceStageCountdown(pendingReferenceStage, referenceStage);
    } else {
        showPendingReferenceStagePrompt(referenceStage);
    }
}

// 自动模式子阶段倒计时：归零后推进 proceedToReferenceStage(nextStage)。
function showReferenceStageCountdown(nextStage, completedStage) {
    renderCountdownMessage(() => {
        addAgentMessage(t('messages.autoEnterNextReferenceStage', { stage: getReferenceStageName(nextStage) }));
        isWaitingForConfirm = false;
        hideStatusSection();
        proceedToReferenceStage(nextStage);
    });
}

// 推进到下一子阶段：videos→进入视频流程；否则 POST /continue_reference_stage。
function proceedToReferenceStage(nextStage) {
    if (nextStage === 'videos') {
        startVideoGenerationAfterReference();
        return;
    }
    // stage 参数传“当前已完成阶段”，后端据 has_category2 计算真实下一阶段。
    fetch('/continue_reference_stage', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            project_id: currentProjectId || '',
            client_id: wsClientId || '',
            stage: referenceStage || 'category1',
            generation_mode: getCurrentVideoMode(),
            ui_language: currentLanguage,
        })
    })
    .then(response => response.json())
    .then(result => {
        if (!result.success) {
            addAgentMessage(t('messages.regenerateFailedWithError', { error: result.error || '' }));
        }
    })
    .catch(error => {
        addAgentMessage(t('messages.regenerateFailedWithError', { error: String(error) }));
    });
}

function getReferenceStageName(stage) {
    const names = {
        category1: t('steps.referenceCategory1'),
        category2: t('steps.referenceCategory2'),
        category3: t('steps.referenceCategory3'),
        videos: t('steps.videosTitle'),
    };
    return names[stage] || stage;
}

// 参考图完成后开始视频生成（新流程：逐个生成+审核）
function startVideoGenerationAfterReference() {
    if (!currentProjectId) {
        alert(t('messages.createProjectFirst'));
        return;
    }

    referenceImageLocked = true;
    currentStep = 'videos';
    refreshAllReferenceActionStates();
    showLoading(t('status.startingVideoGeneration'));

    // 发送请求到后端
    fetch('/continue_generate_after_reference', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            project_id: currentProjectId,
            client_id: wsClientId || '',
            ui_language: currentLanguage,
            review_mode: getCurrentReviewMode(),
            generation_mode: getCurrentVideoMode()
        })
    })
    .then(response => response.json())
    .then(result => {
        hideLoading();
        if (!result.success) {
            referenceImageLocked = false;
            currentStep = 'reference_image';
            refreshAllReferenceActionStates();
            addAgentMessage(t('messages.startVideoFailed', { error: result.error }));
        }
        // 后端通过WebSocket推送进度，不需要额外处理
    })
    .catch(error => {
        hideLoading();
        referenceImageLocked = false;
        currentStep = 'reference_image';
        refreshAllReferenceActionStates();
        console.error('Start video generation error:', error);
        addAgentMessage(t('messages.startVideoFailedRetry'));
    });
}

function addUserMessage(text, images, audio) {
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message user-message';
    
    let contentHtml = '';
    if (text) {
        contentHtml += `<p>${escapeHtml(text)}</p>`;
    }
    if (images && images.length > 0) {
        contentHtml += '<div class="message-images">';
        images.forEach(item => {
            const imageUrl = typeof item === 'string' ? item : item.url;
            contentHtml += `<img src="${imageUrl}" style="max-width: 100px; max-height: 100px; margin: 4px; border-radius: 4px;">`;
        });
        contentHtml += '</div>';
    }
    if (audio) {
        contentHtml += `<p>${t('labels.audioMessage')}</p>`;
    }
    
    messageDiv.innerHTML = `
        <div class="message-content">${contentHtml}</div>
        <div class="message-avatar">👤</div>
    `;
    
    chatMessages.appendChild(messageDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// 添加 Agent 消息
function addAgentMessage(text) {
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message agent-message';
    messageDiv.innerHTML = `
        <div class="message-avatar">🤖</div>
        <div class="message-content">
            <p>${text.replace(/\n/g, '<br>')}</p>
        </div>
    `;
    chatMessages.appendChild(messageDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function handleUseOriginalReferenceChange(event) {
    useOriginalReference = !!event.target.checked && uploadedImages.length > 0;
    updateUploadedFiles();
}

function updateUploadedImageType(index, referenceType) {
    const characterCount = countUploadedImagesByType('character');
    const sceneCount = countUploadedImagesByType('scene');
    const currentType = uploadedImages[index]?.reference_type || 'character';

    if (referenceType === currentType) return;
    if (referenceType === 'character' && characterCount >= frontendConfig.character_reference_max_count) {
        alert(t('messages.characterReferenceLimit', { count: frontendConfig.character_reference_max_count }));
        updateUploadedFiles();
        return;
    }
    if (referenceType === 'scene' && sceneCount >= frontendConfig.scene_reference_max_count) {
        alert(t('messages.sceneReferenceLimit', { count: frontendConfig.scene_reference_max_count }));
        updateUploadedFiles();
        return;
    }
    uploadedImages[index].reference_type = referenceType;
    updateUploadedFiles();
}

function updateUploadedImageName(index, value) {
    if (!uploadedImages[index]) return;
    uploadedImages[index].name = value;
}

// 处理图片上传
async function handleImageUpload(event) {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    if (uploadedImages.length + files.length > frontendConfig.reference_image_max_count) {
        alert(t('messages.referenceImageLimit', { count: frontendConfig.reference_image_max_count }));
        imageInput.value = '';
        return;
    }
    
    showLoading(t('status.uploadingImages'));
    const draftProjectId = ensureDraftProjectId();
    
    for (const file of files) {
        try {
            const url = await uploadFile(file, draftProjectId);
            const nextCharacterCount = countUploadedImagesByType('character') + 1;
            const defaultType = nextCharacterCount <= frontendConfig.character_reference_max_count ? 'character' : 'scene';
            uploadedImages.push({
                url,
                reference_type: defaultType,
                name: ''
            });
        } catch (error) {
            console.error('Image upload error:', error);
            alert(t('messages.imageUploadFailed'));
        }
    }
    
    updateUploadedFiles();
    hideLoading();
    
    // 清空 input
    imageInput.value = '';
}

// 上传文件
async function uploadFile(file, projectId = '') {
    // 云端网关偶发 "upstream request timeout" 等非 JSON 明文错误；
    // 直接 response.json() 会抛 SyntaxError 使上传硬失败。这里做健壮解析 + 一次重试。
    const attemptUpload = async () => {
        const formData = new FormData();
        formData.append('file', file);
        if (projectId) {
            formData.append('project_id', projectId);
        }

        const response = await fetch('/upload', {
            method: 'POST',
            body: formData
        });

        const rawText = await response.text();
        let result;
        try {
            result = JSON.parse(rawText);
        } catch (parseError) {
            // 非 JSON（多为网关超时/网关错误页），抛出可读错误以触发重试。
            throw new Error(`gateway_error: HTTP ${response.status} ${rawText.slice(0, 80)}`);
        }

        if (result.success) {
            return result.url;
        }
        throw new Error(result.error || 'upload_failed');
    };

    try {
        return await attemptUpload();
    } catch (firstError) {
        console.warn('Upload attempt failed, retrying once:', firstError);
        await new Promise((resolve) => setTimeout(resolve, 1500));
        return await attemptUpload();
    }
}

// 更新已上传文件显示
function updateUploadedFiles() {
    uploadedFiles.innerHTML = '';
    
    uploadedImages.forEach((item, index) => {
        const fileDiv = document.createElement('div');
        fileDiv.className = 'uploaded-file';
        const currentType = item.reference_type || 'character';
        const currentName = String(item.name || '').trim();
        const missingName = !currentName;
        const namePlaceholder = currentType === 'character'
            ? t('labels.referenceNameCharacterPlaceholder')
            : t('labels.referenceNameScenePlaceholder');
        fileDiv.innerHTML = `
            <img src="${item.url}" alt="${t('labels.referenceImage')}">
            <div style="display:flex; flex-direction:column; gap:6px; min-width:160px;">
                <span>${t('labels.image', { index: index + 1 })}</span>
                <select onchange="updateUploadedImageType(${index}, this.value)">
                    <option value="character" ${currentType === 'character' ? 'selected' : ''}>${t('labels.referenceTypeCharacter')}</option>
                    <option value="scene" ${currentType === 'scene' ? 'selected' : ''}>${t('labels.referenceTypeScene')}</option>
                </select>
                <input type="text" value="${escapeHtml(item.name || '')}" placeholder="${namePlaceholder}" oninput="updateUploadedImageName(${index}, this.value)" style="${missingName ? 'border:1px solid #ff4d4f; background:#fff2f0;' : ''}">
            </div>
            <span class="remove-btn" onclick="removeImage(${index})">✕</span>
        `;
        uploadedFiles.appendChild(fileDiv);
    });
    
    if (uploadedAudio) {
        const fileDiv = document.createElement('div');
        fileDiv.className = 'uploaded-file';
        fileDiv.innerHTML = `
            <span>${t('labels.audioRecording')}</span>
            <span class="remove-btn" onclick="removeAudio()">✕</span>
        `;
        uploadedFiles.appendChild(fileDiv);
    }

    if (uploadedImages.length > 0) {
        const toggleDiv = document.createElement('label');
        toggleDiv.className = 'uploaded-file use-original-toggle';
        toggleDiv.style.display = 'flex';
        toggleDiv.style.alignItems = 'center';
        toggleDiv.style.gap = '8px';
        toggleDiv.style.cursor = 'pointer';
        toggleDiv.innerHTML = `
            <input type="checkbox" ${useOriginalReference ? 'checked' : ''} onchange="handleUseOriginalReferenceChange(event)">
            <span>${t('labels.useOriginalImage')}</span>
        `;
        uploadedFiles.appendChild(toggleDiv);
    } else {
        useOriginalReference = false;
    }
}

// 移除图片
function removeImage(index) {
    uploadedImages.splice(index, 1);
    if (uploadedImages.length === 0) {
        useOriginalReference = false;
    }
    updateUploadedFiles();
}

// 移除音频
function removeAudio() {
    uploadedAudio = null;
    updateUploadedFiles();
}

function getAudioFileExtension(mimeType) {
    const normalized = (mimeType || '').toLowerCase().split(';')[0].trim();
    const extensionByMimeType = {
        'audio/webm': 'webm',
        'video/webm': 'webm',
        'audio/ogg': 'ogg',
        'audio/opus': 'opus',
        'audio/mp4': 'm4a',
        'audio/mpeg': 'mp3',
        'audio/mp3': 'mp3',
        'audio/aac': 'aac',
        'audio/wav': 'wav',
        'audio/x-wav': 'wav'
    };
    return extensionByMimeType[normalized] || 'webm';
}

// 开始录音
async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const preferredMimeTypes = [
            'audio/webm;codecs=opus',
            'audio/webm',
            'audio/ogg;codecs=opus',
            'audio/mp4'
        ];
        const supportedMimeType = preferredMimeTypes.find(type =>
            window.MediaRecorder && MediaRecorder.isTypeSupported(type)
        );
        mediaRecorder = supportedMimeType
            ? new MediaRecorder(stream, { mimeType: supportedMimeType })
            : new MediaRecorder(stream);
        audioChunks = [];
        
        mediaRecorder.ondataavailable = (event) => {
            audioChunks.push(event.data);
        };
        
        mediaRecorder.onstop = () => {
            const mimeType = mediaRecorder.mimeType || audioChunks[0]?.type || supportedMimeType || 'audio/webm';
            const audioBlob = new Blob(audioChunks, { type: mimeType });
            const extension = getAudioFileExtension(mimeType);
            uploadedAudio = new File([audioBlob], `recording.${extension}`, { type: mimeType });
            updateUploadedFiles();
            
            // 停止所有轨道
            stream.getTracks().forEach(track => track.stop());
            
            // 自动上传并识别
            processAudioRecording();
        };
        
        mediaRecorder.start();
        isRecording = true;
        micBtn.classList.add('recording');
        recordingIndicator.style.display = 'flex';
        micBtn.title = t('input.stopRecording');
        
    } catch (error) {
        console.error('Recording error:', error);
        alert(t('messages.audioProcessingFailed'));
    }
}

// 停止录音
function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
        isRecording = false;
        micBtn.classList.remove('recording');
        recordingIndicator.style.display = 'none';
        micBtn.title = t('input.startRecording');
    }
}

// 处理录音文件 - 上传并ASR识别
async function processAudioRecording() {
    if (!uploadedAudio) return;
    
    showLoading(t('status.processingAudio'));
    
    try {
        // 上传音频文件
        const audioUrl = await uploadFile(uploadedAudio, currentProjectId || ensureDraftProjectId());
        
        // 调用ASR识别
        const response = await fetch('/asr', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                audio_url: audioUrl
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            // 将识别结果填入输入框
            textInput.value = result.text;
            addAgentMessage(t('messages.audioRecognitionResult', { text: result.text }));
        } else {
            addAgentMessage(t('messages.audioRecognitionFailed', { error: result.error }));
        }
        
    } catch (error) {
        console.error('Audio processing error:', error);
        addAgentMessage(t('messages.audioProcessingFailed'));
    }
    
    hideLoading();
}

// 处理 Agent 输出
function handleAgentOutput(agent, output) {
    hideLoading();
    switch (agent) {
        case 'script_agent':
            displayScript(output);
            break;
        case 'image_agent':
            // 支持两步图片生成：先显示参考图库，用户确认后再显示分镜图片
            if (output.step === 'reference_image') {
                displayReferenceImage(output);
            } else {
                displayImages(output);
            }
            break;
        case 'video_agent':
            displayVideos(output);
            break;
        case 'video_review_agent':
            // 视频审核结果输出
            displayVideoReviewResult(output);
            break;
        case 'storyboard_review_agent':
            // 故事版审核结果（自动重生成，前端仅记录日志，最终以刷新后的故事版为准）
            console.log('[storyboard_review]', output);
            break;
        case 'comic_pdf_agent':
            displayComicPdfLink(output);
            break;
        case 'merge_agent':
            // 检查是否是合成开始（没有final_video_url）还是合成完成
            if (output.final_video_url) {
                // 合成完成，显示最终视频
                displayFinalVideo(output.final_video_url);
            } else {
                // 合成开始，显示转转效果
                showMergeStep();
            }
            break;
    }
}

// 显示视频审核结果
function displayVideoReviewResult(output) {
    videoReviewOutputsByScene[output.scene_number] = { ...output };
    const sceneNumber = output.scene_number;
    const approved = output.approved;
    const score = output.score;
    const feedback = output.feedback || '';
    const retryCount = output.retry_count || 0;
    const maxRetries = output.max_retries || 3;
    const message = output.message || '';
    const totalScenes = output.total_scenes || null;
    const status = output.status || '';
    const acceptedOverRetry = !!output.accepted_over_retry;
    const manualContinueAllowed = !!output.manual_continue_allowed;
    const reviewMode = output.review_mode || '';
    const manualPauseRequired = reviewMode === 'manual' && manualContinueAllowed;
    const nextStep = output.next_step || (output.is_last_scene ? 'merge' : 'videos');

    if (totalScenes) {
        videoTotalScenes = totalScenes;
    }

    // 确保该分镜的视频卡片存在（避免审核先于视频渲染到达时丢失展示）
    ensureVideosContainer();
    ensureVideoItem(sceneNumber);
    const reviewEl = ensureReviewEl(sceneNumber);
    hideSkipSceneButton(sceneNumber);

    // 审核中：在该分镜区域显示转转 + “正在审核...”
    if (status === 'reviewing') {
        setVideoItemLoading(sceneNumber, t('labels.reviewing'));
        reviewEl.style.background = '#f0f7ff';
        reviewEl.style.border = '1px solid #91caff';
        reviewEl.style.color = '#1677ff';
        reviewEl.className = 'video-review-status is-reviewing';
        reviewEl.innerHTML = `
            <div class="review-status-title">${t('labels.reviewing')}</div>
            <div class="review-status-text">${message || ''}</div>
        `;
        renderStatusBar(t('status.reviewingScene', { scene: sceneNumber }), 'loading', t('steps.review'));
        return;
    }

    // 根据审核结果显示不同样式
    if (acceptedOverRetry) {
        reviewEl.style.background = '#fff7e6';
        reviewEl.style.border = '1px solid #ffd591';
        reviewEl.style.color = '#fa8c16';
        reviewEl.className = 'video-review-status is-accepted-over-retry';
        reviewEl.innerHTML = `
            <div class="review-status-title">${t('labels.acceptedOverRetry')}</div>
            <div class="review-status-text">${t('labels.score', { score })} | ${t('labels.retryCount', { count: retryCount })}</div>
        `;
        clearVideoItemLoading(sceneNumber);
        markVideoReviewed(sceneNumber, true);
    } else if (approved) {
        reviewEl.style.background = '#f6ffed';
        reviewEl.style.border = '1px solid #b7eb8f';
        reviewEl.style.color = '#52c41a';
        reviewEl.className = 'video-review-status is-passed';
        reviewEl.innerHTML = `
            <div class="review-status-title">${t('labels.reviewPassed')}</div>
            <div class="review-status-text">${t('labels.score', { score })}</div>
        `;
        clearVideoItemLoading(sceneNumber);
        markVideoReviewed(sceneNumber, true);
        if (manualPauseRequired) {
            isWaitingForConfirm = true;
            pendingStep = nextStep;
            currentStep = 'videos';
            renderStatusBar(message || t('labels.reviewPassed'), 'info', t('steps.video'));
        }
    } else if (manualContinueAllowed) {
        reviewEl.style.background = 'linear-gradient(135deg, #fff7e6 0%, #f9f0ff 100%)';
        reviewEl.style.border = '1px solid #d3adf7';
        reviewEl.style.color = '#531dab';
        reviewEl.className = 'video-review-status is-manual-continue';
        reviewEl.innerHTML = `
            <div class="review-status-header">
                <span class="review-status-title">${t('labels.reviewManualContinue')}</span>
                <span class="review-mode-badge">${t('labels.manualModeBadge')}</span>
            </div>
            <div class="review-status-text">${t('labels.score', { score })}</div>
            <div class="review-status-feedback">${feedback}</div>
            <div class="review-status-hint review-status-hint-strong">${t('labels.reviewHintManualContinue')}</div>
        `;
        clearVideoItemLoading(sceneNumber);
        markVideoReviewed(sceneNumber, false);
        isWaitingForConfirm = true;
        pendingStep = nextStep;
        currentStep = 'videos';
        renderStatusBar(t('status.sceneManualContinue', { scene: sceneNumber }), 'info', t('steps.video'));
    } else if (retryCount >= maxRetries) {
        reviewEl.style.background = '#fff7e6';
        reviewEl.style.border = '1px solid #ffd591';
        reviewEl.style.color = '#fa8c16';
        reviewEl.className = 'video-review-status is-accepted-over-retry';
        reviewEl.innerHTML = `
            <div class="review-status-title">${t('labels.acceptedOverRetry')}</div>
            <div class="review-status-text">${t('labels.score', { score })} | ${t('labels.retryCount', { count: retryCount })}</div>
        `;
        clearVideoItemLoading(sceneNumber);
        // 达到最大重试次数后继续下一步，这里也视为“审核完成”
        markVideoReviewed(sceneNumber, false);
    } else {
        // 审核未通过，需要重新生成（auto: 会自动重试；manual: 前端提示用户手动点“重新生成”）
        reviewEl.style.background = '#fff2f0';
        reviewEl.style.border = '1px solid #ffccc7';
        reviewEl.style.color = '#ff4d4f';
        reviewEl.className = 'video-review-status is-rejected';
        reviewEl.innerHTML = `
            <div class="review-status-title">${t('labels.reviewRejected')}</div>
            <div class="review-status-text">${t('labels.score', { score })} | ${t('labels.retryCurrent', { count: retryCount })}</div>
            <div class="review-status-feedback">${feedback}</div>
            <div class="review-status-hint">${t('labels.reviewHintAutoRetry')}</div>
        `;
        setVideoItemLoading(sceneNumber, t('status.sceneRegenerating', { scene: sceneNumber }));
        renderStatusBar(t('status.sceneRejectedRegenerating', { scene: sceneNumber }), 'loading', t('steps.video'));
    }

    updateVideoStepProgressUI();
    maybeStartPendingStepCountdown();
}

function ensureVideosContainer() {
    // 查找或创建视频容器
    let videosContainer = document.getElementById('videos-container');
    if (!videosContainer) {
        videosContainer = document.createElement('div');
        videosContainer.id = 'videos-container';
        videosContainer.className = 'videos-container';
        videosContainer.style.marginTop = '20px';
        videosContainer.innerHTML = `<h4 style="margin-bottom: 15px; color: #333; border-bottom: 2px solid #1890ff; padding-bottom: 8px;">${t('labels.generatedVideos')}</h4>`;

        const grid = document.createElement('div');
        grid.className = 'videos-grid';
        grid.id = 'videos-grid';
        videosContainer.appendChild(grid);

        const imagesCard = document.getElementById('images-card');
        if (imagesCard) {
            imagesCard.appendChild(videosContainer);
        } else {
            contentDisplay.appendChild(videosContainer);
        }
    }
    return document.getElementById('videos-grid');
}

function ensureVideoItem(sceneNumber) {
    const grid = ensureVideosContainer();
    let item = document.getElementById(`video-item-${sceneNumber}`);
    if (item) return item;
    const linkAttrs = getExternalLinkAttrs();

    item = document.createElement('div');
    item.className = 'video-item';
    item.id = `video-item-${sceneNumber}`;

    item.innerHTML = `
        <div class="video-label">${t('labels.scene', { scene: sceneNumber })}</div>
        <div class="video-thumb" style="position: relative; cursor: pointer; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);" onclick="openVideoModalIfReady(${sceneNumber})">
            <div id="video-placeholder-${sceneNumber}" class="video-placeholder">${t('labels.waitingGenerate')}</div>
            <video id="video-src-${sceneNumber}" src="" style="width: 100%; height: 120px; object-fit: cover; display: none;" preload="metadata" muted playsinline></video>
            <div class="video-play-overlay" style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.3); display: none; align-items: center; justify-content: center; transition: background 0.3s;">
                <div class="play-icon" style="width: 50px; height: 50px; background: rgba(255,255,255,0.9); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 20px; color: #333;">▶</div>
            </div>
        </div>
        <div class="item-actions" style="display: flex; gap: 8px; flex-wrap: nowrap; white-space: nowrap; margin: 10px;">
            <button class="item-btn regenerate" onclick="event.stopPropagation(); regenerateVideo(${sceneNumber})" style="white-space: nowrap; flex-shrink: 0; padding: 6px 12px; font-size: 12px; background: #faad14; color: white; border: none; border-radius: 4px; cursor: pointer;">${t('actions.regenerate')}</button>
            <button class="item-btn skip" id="video-skip-${sceneNumber}" onclick="event.stopPropagation(); skipScene(${sceneNumber})" style="display: none; white-space: nowrap; flex-shrink: 0; padding: 6px 12px; font-size: 12px; background: #ff7875; color: white; border: none; border-radius: 4px; cursor: pointer;">${t('actions.skip')}</button>
            <a class="item-btn download" href="" ${linkAttrs} onclick="event.stopPropagation()" style="white-space: nowrap; flex-shrink: 0; padding: 6px 12px; font-size: 12px; background: #1890ff; color: white; border: none; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block;">${t('actions.download')}</a>
        </div>
        <div id="video-review-${sceneNumber}" class="video-review-status">
            <div class="review-status-title">${t('labels.waitingGenerateReview')}</div>
        </div>
    `;

    grid.appendChild(item);
    return item;
}

function ensureReviewEl(sceneNumber) {
    const el = document.getElementById(`video-review-${sceneNumber}`);
    return el;
}

function getVideoUrl(sceneNumber) {
    const v = document.getElementById(`video-src-${sceneNumber}`);
    return v ? (v.getAttribute('src') || '') : '';
}

function openVideoModalIfReady(sceneNumber) {
    const url = getVideoUrl(sceneNumber);
    if (!url) return;
    openVideoModal(url);
}

function updateVideoVisualState(sceneNumber, pendingText = '') {
    const item = ensureVideoItem(sceneNumber);
    const video = item.querySelector(`#video-src-${sceneNumber}`);
    const placeholder = item.querySelector(`#video-placeholder-${sceneNumber}`);
    const playOverlay = item.querySelector('.video-play-overlay');
    const hasUrl = !!(video && video.getAttribute('src'));

    if (video) {
        video.style.display = hasUrl ? 'block' : 'none';
    }
    if (playOverlay) {
        playOverlay.style.display = hasUrl ? 'flex' : 'none';
    }
    if (placeholder) {
        placeholder.textContent = pendingText || t('labels.waitingGenerate');
        placeholder.style.display = hasUrl ? 'none' : 'flex';
    }
}

function setVideoItemUrl(sceneNumber, url) {
    const item = ensureVideoItem(sceneNumber);
    const video = item.querySelector(`#video-src-${sceneNumber}`);
    const download = item.querySelector('a.item-btn.download');
    const placeholder = item.querySelector(`#video-placeholder-${sceneNumber}`);
    const playOverlay = item.querySelector('.video-play-overlay');

    if (video) {
        video.src = url;
        // 立即显示视频元素，不依赖 getAttribute
        video.style.display = 'block';
        video.style.width = '100%';
        video.style.height = '120px';
        video.style.objectFit = 'cover';
    }
    if (download) {
        download.href = url;
        download.target = '_blank';
        download.rel = 'noopener noreferrer';
    }

    // 立即更新视觉状态 - 隐藏 placeholder，显示播放按钮
    if (placeholder) {
        placeholder.style.display = 'none';
    }
    if (playOverlay) {
        playOverlay.style.display = 'flex';
    }

    // 给视频项添加 has-video 类，用于 CSS 样式控制
    item.classList.add('has-video');
    item.classList.add('newly-generated');

    // 1秒后移除动画类
    setTimeout(() => {
        item.classList.remove('newly-generated');
    }, 1000);

}

function setVideoItemLoading(sceneNumber, text) {
    const item = ensureVideoItem(sceneNumber);
    regeneratingVideoSceneNumbers.add(Number(sceneNumber));
    cancelAutoRunCountdown();
    updateVideoVisualState(sceneNumber, text || t('status.generating'));

    const thumb = item.querySelector('.video-thumb');
    if (!thumb) return;

    let overlay = thumb.querySelector('.item-generating-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'item-generating-overlay';
        thumb.appendChild(overlay);
    }

    overlay.innerHTML = `
        <div class="item-generating-spinner"></div>
        <div class="item-generating-text">${text || t('status.generating')}</div>
    `;

    const regenerateBtn = item.querySelector('button.item-btn.regenerate');
    if (regenerateBtn) {
        regenerateBtn.disabled = true;
        regenerateBtn.style.opacity = '0.65';
        regenerateBtn.style.cursor = 'not-allowed';
    }
    const skipBtn = item.querySelector('button.item-btn.skip');
    if (skipBtn) {
        skipBtn.disabled = true;
        skipBtn.style.opacity = '0.65';
        skipBtn.style.cursor = 'not-allowed';
    }
}

function clearVideoItemLoading(sceneNumber) {
    const item = document.getElementById(`video-item-${sceneNumber}`);
    if (!item) return;
    regeneratingVideoSceneNumbers.delete(Number(sceneNumber));
    const overlay = item.querySelector('.video-thumb .item-generating-overlay');
    if (overlay) overlay.remove();
    const regenerateBtn = item.querySelector('button.item-btn.regenerate');
    if (regenerateBtn) {
        regenerateBtn.disabled = false;
        regenerateBtn.style.opacity = '';
        regenerateBtn.style.cursor = '';
    }
    const skipBtn = item.querySelector('button.item-btn.skip');
    if (skipBtn) {
        skipBtn.disabled = false;
        skipBtn.style.opacity = '';
        skipBtn.style.cursor = '';
    }
    updateVideoVisualState(sceneNumber);
}

function showSkipSceneButton(sceneNumber) {
    const item = ensureVideoItem(sceneNumber);
    const skipBtn = item.querySelector('button.item-btn.skip');
    if (!skipBtn) return;
    skipBtn.style.display = 'inline-block';
    skipBtn.disabled = false;
    skipBtn.style.opacity = '';
    skipBtn.style.cursor = '';
}

function hideSkipSceneButton(sceneNumber) {
    const item = document.getElementById(`video-item-${sceneNumber}`);
    if (!item) return;
    const skipBtn = item.querySelector('button.item-btn.skip');
    if (!skipBtn) return;
    skipBtn.style.display = 'none';
    skipBtn.disabled = false;
    skipBtn.style.opacity = '';
    skipBtn.style.cursor = '';
}

function removeVideoItem(sceneNumber) {
    delete videoOutputsByScene[sceneNumber];
    delete videoReviewOutputsByScene[sceneNumber];
    delete videoSceneState[sceneNumber];
    const item = document.getElementById(`video-item-${sceneNumber}`);
    if (item) {
        item.remove();
    }
}

function trimVideoSceneArtifacts(totalScenes) {
    Object.keys(videoOutputsByScene).map(Number).forEach((scene) => {
        if (scene > totalScenes) delete videoOutputsByScene[scene];
    });
    Object.keys(videoReviewOutputsByScene).map(Number).forEach((scene) => {
        if (scene > totalScenes) delete videoReviewOutputsByScene[scene];
    });
    Object.keys(videoSceneState).map(Number).forEach((scene) => {
        if (scene > totalScenes) delete videoSceneState[scene];
    });

    const items = document.querySelectorAll('.video-item[id^="video-item-"]');
    items.forEach((item) => {
        const match = item.id.match(/^video-item-(\d+)$/);
        const scene = match ? Number(match[1]) : 0;
        if (scene > totalScenes) {
            item.remove();
        }
    });
}

function handleSceneSkipped(output) {
    const skippedSceneNumber = output.scene_number;
    const totalScenes = Number(output.total_scenes || 0);
    const message = output.message || t('messages.sceneSkipped', { scene: skippedSceneNumber });

    if (output.script) {
        displayScript(output.script);
    }

    clearVideoItemLoading(skippedSceneNumber);
    hideSkipSceneButton(skippedSceneNumber);
    removeVideoItem(skippedSceneNumber);
    videoTotalScenes = totalScenes;
    trimVideoSceneArtifacts(totalScenes);
    updateVideoStepProgressUI();
    renderStatusBar(message, 'info', t('steps.video'));
    addAgentMessage(message);
}

function markVideoGenerated(sceneNumber) {
    if (!videoSceneState[sceneNumber]) videoSceneState[sceneNumber] = { generated: false, reviewed: false };
    videoSceneState[sceneNumber].generated = true;
}

function markVideoReviewed(sceneNumber) {
    if (!videoSceneState[sceneNumber]) videoSceneState[sceneNumber] = { generated: false, reviewed: false };
    videoSceneState[sceneNumber].reviewed = true;
}

function updateVideoStepProgressUI() {
    if (!videoTotalScenes) return;
    let sum = 0;
    for (let i = 1; i <= videoTotalScenes; i++) {
        const st = videoSceneState[i] || { generated: false, reviewed: false };
        // 生成占50%，审核完成占50%
        sum += (st.generated ? 0.5 : 0) + (st.reviewed ? 0.5 : 0);
    }
    const progress = Math.round((sum / videoTotalScenes) * 100);
    stepProgress.videos = progress;
    updateStepHighlight('video_agent', progress);
    updateOverallProgress();
}

// 显示剧本
function displayScript(script) {
    lastScriptData = script;
    currentStep = 'script';
    // 确保 script 是有效的对象
    if (!script) {
        console.error('Invalid script data:', script);
        addAgentMessage(t('messages.invalidScriptData'));
        return;
    }

    // 检查并转换数据格式（处理后端发送的 dict 格式）
    const title = script.title || t('labels.scriptUntitled');
    const style = script.style || t('labels.unspecified');
    const era = script.era || '';
    const background = script.background || '';
    const tone = script.tone || '';
    const total_duration = script.total_duration || 0;
    const characters = script.characters || [];
    const sceneDefinitions = script.scene_definitions || [];
    const scenes = script.scenes || [];

    let card = document.getElementById('script-card');
    if (!card) {
        card = document.createElement('div');
        card.className = 'content-card';
        card.id = 'script-card';
        contentDisplay.appendChild(card);
    }

    let sceneDefinitionsHtml = '';
    if (sceneDefinitions.length > 0) {
        sceneDefinitionsHtml = `
            <div class="scene-definitions-section" style="margin: 15px 0; padding: 15px; background: #f9f0ff; border-radius: 8px;">
                <h5 style="margin: 0 0 10px 0; color: #333;">${t('labels.sceneSettings', { count: sceneDefinitions.length })}</h5>
                ${sceneDefinitions.map((sceneDef) => `
                    <div class="scene-definition-item" style="margin: 8px 0; padding: 10px; background: white; border-radius: 6px; border-left: 3px solid #722ed1;">
                        <span style="font-size: 13px; color: #555;"><strong style="color: #722ed1;">${sceneDef.name || t('labels.scriptUntitled')}</strong>：${sceneDef.description || t('labels.noData')}</span>
                    </div>
                `).join('')}
            </div>
        `;
    }

    const renderCharacterField = (labelKey, value, icon = '') => {
        const text = String(value || '').trim();
        if (!text) return '';
        return `
            <div style="font-size: 13px; color: #555; line-height: 1.45; margin-top: 4px;">
                <span style="color: #777;">${icon}${t(labelKey)}：</span>${escapeHtml(text)}
            </div>
        `;
    };

    const renderSceneOutfits = (outfits) => {
        if (!outfits || typeof outfits !== 'object' || Array.isArray(outfits)) return '';
        const lines = Object.entries(outfits)
            .map(([name, outfit]) => {
                const safeName = String(name || '').trim();
                const safeOutfit = String(outfit || '').trim();
                if (!safeName || !safeOutfit) return '';
                return `<div style="margin: 3px 0;"><strong>${escapeHtml(safeName)}：</strong>${escapeHtml(safeOutfit)}</div>`;
            })
            .filter(Boolean)
            .join('');
        if (!lines) return '';
        return `
            <div style="margin: 8px 0; padding: 10px; background: #fff7e6; border-radius: 6px; border-left: 3px solid #fa8c16;">
                <strong style="color: #d46b08;">${t('labels.characterOutfits')}</strong>
                <div style="margin-top: 5px; color: #555; line-height: 1.55;">${lines}</div>
            </div>
        `;
    };

    const renderSceneState = (sceneState) => {
        const text = String(sceneState || '').trim();
        if (!text) return '';
        return `
            <div style="margin: 8px 0; padding: 10px; background: #f6ffed; border-radius: 6px; border-left: 3px solid #52c41a;">
                <strong style="color: #389e0d;">${t('labels.sceneState')}</strong>
                <p style="margin: 5px 0 0 0; color: #555; line-height: 1.6;">${escapeHtml(text)}</p>
            </div>
        `;
    };

    const renderMultilineSceneText = (value, options = {}) => {
        const text = String(value || '').trim();
        if (!text) {
            return `<div style="margin: 5px 0; color: #999;">${t('labels.noData')}</div>`;
        }
        const normalized = text
            .replace(/\r\n/g, '\n')
            .replace(/[ \t]+([\u4e00-\u9fffA-Za-z0-9_·]{1,12}[：:])/g, '\n$1');
        const lines = normalized
            .split('\n')
            .map(line => line.trim())
            .filter(Boolean);
        const fontStyle = options.italic ? 'font-style: italic;' : '';
        return `
            <div style="margin: 5px 0; color: ${options.color || '#555'}; line-height: 1.65; ${fontStyle}">
                ${lines.map(line => `<div>${escapeHtml(line)}</div>`).join('')}
            </div>
        `;
    };

    // 构建角色信息
    let charactersHtml = '';
    if (characters.length > 0) {
        charactersHtml = `
            <div class="characters-section" style="margin: 15px 0; padding: 15px; background: #f5f5f5; border-radius: 8px;">
                <h5 style="margin: 0 0 10px 0; color: #333;">${t('labels.characterSetting', { count: characters.length })}</h5>
                ${characters.map(char => `
                    <div class="character-item" style="margin: 8px 0; padding: 10px; background: white; border-radius: 6px; border-left: 3px solid #1890ff;">
                        <strong style="color: #1890ff;">${escapeHtml(char.name || t('labels.scriptUntitled'))}</strong>
                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0 14px; margin-top: 6px;">
                            ${renderCharacterField('labels.gender', char.gender)}
                            ${renderCharacterField('labels.age', char.age)}
                            ${renderCharacterField('labels.nationality', char.nationality)}
                            ${renderCharacterField('labels.faceFeatures', [char.face_features, char.skin_tone].filter(Boolean).join(' '))}
                            ${renderCharacterField('labels.hairstyle', char.hairstyle)}
                            ${renderCharacterField('labels.bodyFeatures', char.body_features)}
                            ${renderCharacterField('labels.usualOutfit', char.clothing, '👔 ')}
                            ${renderCharacterField('labels.personalityTraits', char.personality, '🧠 ')}
                            ${renderCharacterField('labels.identityBackground', char.identity_background)}
                            ${renderCharacterField('labels.voice', char.voice_type, '🎤 ')}
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    }

    // 构建分镜详情 - 显示完整信息
    let scenesHtml = '';
    if (scenes.length > 0) {
        scenesHtml = scenes.map((scene, index) => {
            // 构建角色呈现信息
            let charactersPresentHtml = '';
            if (scene.characters_present && scene.characters_present.length > 0) {
                charactersPresentHtml = `<span style="color: #1890ff; font-weight: 500;">${escapeHtml(scene.characters_present.join(', '))}</span>`;
            }

            return `
                <div class="scene-item" style="margin: 15px 0; padding: 15px; background: #fafafa; border-radius: 8px; border: 1px solid #e8e8e8;">
                    <h5 style="margin: 0 0 12px 0; color: #333; border-bottom: 2px solid #1890ff; padding-bottom: 8px;">
                        ${t('labels.scene', { scene: scene.scene_number || index + 1 })}
                        <span style="font-size: 12px; color: #666; font-weight: normal;">${t('labels.sceneDuration', { duration: scene.duration || 6 })}</span>
                    </h5>

                    ${renderSceneOutfits(scene.character_outfits)}
                    ${renderSceneState(scene.scene_state)}

                    <div style="margin: 8px 0;">
                        <strong style="color: #333;">${t('labels.sceneDescription')}</strong>
                        <p style="margin: 5px 0; color: #555; line-height: 1.6;">${escapeHtml(scene.description || t('labels.noData'))}</p>
                    </div>

                    <div style="margin: 8px 0; padding: 10px; background: #e6f7ff; border-radius: 6px; border-left: 3px solid #1890ff;">
                        <strong style="color: #1890ff;">${t('labels.dialogue')}</strong>
                        ${renderMultilineSceneText(scene.dialogue, { color: '#333', italic: true })}
                    </div>

                    <div style="margin: 8px 0;">
                        <strong style="color: #333;">${t('labels.characterAction')}</strong>
                        ${renderMultilineSceneText(scene.character_description)}
                    </div>

                    <div style="margin: 8px 0;">
                        <strong style="color: #333;">${t('labels.voiceDescription')}</strong>
                        ${renderMultilineSceneText(scene.voice_description)}
                    </div>

                    <div style="display: flex; flex-wrap: wrap; gap: 15px; margin-top: 10px; padding-top: 10px; border-top: 1px dashed #ddd;">
                        <span style="font-size: 13px;"><strong>${t('labels.mood')}</strong> <span style="color: #722ed1;">${scene.mood || t('labels.noData')}</span></span>
                        <span style="font-size: 13px;"><strong>${t('labels.camera')}</strong> <span style="color: #13c2c2;">${scene.camera_angle || t('labels.noData')}</span></span>
                        ${charactersPresentHtml ? `<span style="font-size: 13px;"><strong>${t('labels.charactersPresent')}</strong> ${charactersPresentHtml}</span>` : ''}
                        <span style="font-size: 13px;"><strong>${t('labels.sceneUsage')}</strong> <span style="color: #722ed1;">${scene.scene_name || t('labels.noData')}</span></span>
                    </div>
                </div>
            `;
        }).join('');
    } else {
        scenesHtml = `<div style="padding: 20px; text-align: center; color: #999;">${t('labels.noSceneData')}</div>`;
    }

    card.innerHTML = `
        <h4 style="margin: 0 0 15px 0; color: #333; border-bottom: 2px solid #1890ff; padding-bottom: 10px;">
            ${t('labels.scriptCardTitle', { title })}
        </h4>
        <div class="script-content">
            <div style="display: flex; flex-wrap: wrap; gap: 20px; margin-bottom: 15px;">
                <p style="margin: 0;"><strong>${t('labels.style')}</strong> <span style="color: #1890ff;">${style}</span></p>
                ${era ? `<p style="margin: 0;"><strong>${t('labels.era')}</strong> <span style="color: #13c2c2;">${era}</span></p>` : ''}
                ${tone ? `<p style="margin: 0;"><strong>${t('labels.tone')}</strong> <span style="color: #eb2f96;">${tone}</span></p>` : ''}
                <p style="margin: 0;"><strong>${t('labels.totalDuration')}</strong> <span style="color: #52c41a;">${t('labels.seconds', { count: total_duration })}</span></p>
                <p style="margin: 0;"><strong>${t('labels.sceneCount')}</strong> <span style="color: #722ed1;">${t('labels.countUnit', { count: scenes.length })}</span></p>
            </div>
            ${background ? `<p style="margin: 0 0 15px 0;"><strong>${t('labels.background')}</strong> <span style="color: #555;">${background}</span></p>` : ''}

            ${charactersHtml}
            ${sceneDefinitionsHtml}

            <div class="scenes-list">
                <h5 style="margin: 20px 0 10px 0; color: #333; font-size: 16px;">${t('labels.sceneDetails', { count: scenes.length })}</h5>
                ${scenesHtml}
            </div>

            <div class="item-actions" style="display:flex; gap:8px; flex-wrap: wrap; margin-top: 18px;">
                <button
                    class="item-btn regenerate"
                    onclick="regenerateScriptFromCard()"
                    style="background: #faad14; color: white; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer;"
                >${t('actions.regenerate')}</button>
            </div>
        </div>
    `;
    contentDisplay.scrollTop = contentDisplay.scrollHeight;
}

// 显示统一参考图库
function displayReferenceImage(output) {
    lastReferenceImageOutput = output;
    currentStep = 'reference_image';
    referenceImageLocked = false;
    // 高亮参考图步骤
    updateStepHighlight('image_agent', 100, 'reference');
    stepProgress.reference_image = 100;
    updateOverallProgress();

    // 检查是否已存在参考图卡片，如果存在则更新
    let card = document.getElementById('reference-image-card');

    if (!card) {
        card = document.createElement('div');
        card.className = 'content-card';
        card.id = 'reference-image-card';
        contentDisplay.appendChild(card);
    }

    const characterImages = output.character_images || [];
    const sceneImages = output.scene_images || [];
    const allImages = output.images || [...characterImages, ...sceneImages];
    if (allImages.length === 0) return;
    referenceImageRegenerateLocked = allImages.every((item) => !!item.regenerate_locked);
    const isReferenceGenerationComplete = canStartReferenceStepCountdown(output);
    // 子阶段完成态：本阶段资产齐全（stage_ready）但整套尚未 ready_for_confirmation（category1/category2）。
    const isReferenceStageComplete = !!(output && output.stage_ready === true);

    if (hasPendingReferenceRegeneration()) {
        // 仍有参考图/装扮/布景状态/故事版在重新生成中：状态栏必须保持“图片重新生成中”，
        // 不能因为 reference_output 携带 ready_for_confirmation 就误显示“已生成完成，请确认”。
        renderStatusBar(t('labels.referenceImageRegeneratingText'), 'loading', t('steps.referenceImageTitle'));
    } else if (isReferenceGenerationComplete || isReferenceStageComplete) {
        showStatusSection(output.message || t('messages.referenceCompleteStatus'));
    } else {
        renderStatusBar(t('progress.reference.generating'), 'loading', t('steps.referenceImageTitle'));
    }

    const linkAttrs = getExternalLinkAttrs();
    const renderLibraryItems = (items, referenceType) => {
        return items.map((item) => `
            <div style="display:flex; flex-direction:column; gap:8px; width:180px; padding: 12px; border: 1px solid #f0f0f0; border-radius: 10px; background: #fff; font-size: 13px; line-height: 1.5;">
                <div style="position: relative; width: 100%; cursor: pointer; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.12);" onclick="openMediaModal('image', '${item.url}')">
                    <img src="${item.url}" alt="${escapeHtml(item.name || t('labels.referenceImage'))}" style="width:100%; height:180px; object-fit:cover; display:block;">
                    <div style="position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.6); color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px;">
                        ${t('labels.clickToZoom')}
                    </div>
                </div>
                <div style="font-size: 13px; color: #888;">${referenceType === 'scene' ? t('labels.referenceTypeScene') : t('labels.referenceTypeCharacter')}</div>
                <div style="font-size: 16px; font-weight:600; color:#333; line-height: 1.4;">${escapeHtml(item.name || t('labels.referenceImage'))}</div>
                ${item.regenerate_locked ? `<div style="font-size: 13px; color: #666; line-height: 1.5;">${t('labels.referenceImageUsingOriginal')}</div>` : ''}
                <div class="item-actions" style="display:flex; gap:8px; flex-wrap: wrap;">
                    <button
                        class="item-btn regenerate reference-regenerate-btn"
                        data-locked="${item.regenerate_locked ? 'true' : 'false'}"
                        data-reference-key="${escapeHtml(buildReferenceAssetKey(referenceType, item.name || ''))}"
                        onclick="event.stopPropagation(); regenerateReferenceAsset('${referenceType}', '${encodeURIComponent(item.name || '')}', ${Number.isFinite(Number(item.slot_index)) ? Number(item.slot_index) : -1})"
                        style="background: #faad14; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer;"
                    >${t('actions.regenerate')}</button>
                    <a href="${item.url}" ${linkAttrs} class="item-btn download" style="background: #1890ff; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; width: fit-content;">${t('actions.download')}</a>
                </div>
            </div>
        `).join('');
    };
    card.innerHTML = `
        <h4 style="margin: 0 0 15px 0; font-size: 18px; font-weight: 600; color: #333; border-bottom: 2px solid #1890ff; padding-bottom: 10px;">${t('labels.referenceLibraryUnified')}</h4>
        <div class="reference-image-container" style="display: flex; flex-direction: column; gap: 20px; font-size: 14px; color: #333; line-height: 1.6;">
            ${characterImages.length > 0 ? `<div><h5 style="margin:0 0 12px 0; font-size: 16px; font-weight: 600; color: #333;">${t('labels.referenceLibraryCharacters')}</h5><div style="display:flex; flex-wrap:wrap; gap:16px;">${renderLibraryItems(characterImages, 'character')}</div></div>` : ''}
            ${sceneImages.length > 0 ? `<div><h5 style="margin:0 0 12px 0; font-size: 16px; font-weight: 600; color: #333;">${t('labels.referenceLibraryScenes')}</h5><div style="display:flex; flex-wrap:wrap; gap:16px;">${renderLibraryItems(sceneImages, 'scene')}</div></div>` : ''}
        </div>
    `;

    refreshReferenceImageActionState();

    // 在布景参考图库之后渲染“各分镜-角色装扮-布景状态”模块。
    displayVariantAssets(output);
    // 在角色装扮/布景状态模块之后、分镜视频之前渲染“各分镜故事版”模块。
    displayStoryboards(output);

    contentDisplay.scrollTop = contentDisplay.scrollHeight;

    // 仅在参考图库全部生成完毕后进入确认和倒计时
    if (isReferenceGenerationComplete) {
        isWaitingForConfirm = true;
        pendingStep = 'videos';
        maybeStartPendingStepCountdown();
    }
}

// 渲染“各分镜-角色装扮-布景状态”模块：位于布景参考图库之下，布局参照参考图库。
function displayVariantAssets(output) {
    const outfitImages = (output && output.character_outfit_images) || [];
    const sceneStateImages = (output && output.scene_state_images) || [];
    const existing = document.getElementById('variant-assets-card');

    if (!outfitImages.length && !sceneStateImages.length) {
        if (existing) existing.remove();
        return;
    }

    let card = existing;
    if (!card) {
        card = document.createElement('div');
        card.className = 'content-card';
        card.id = 'variant-assets-card';
    }

    // 保证顺序：参考图库 → 角色装扮/布景状态 → 故事版。插入到参考图库卡片之后。
    const referenceCard = document.getElementById('reference-image-card');
    if (referenceCard && referenceCard.parentNode === contentDisplay) {
        if (referenceCard.nextSibling !== card) {
            contentDisplay.insertBefore(card, referenceCard.nextSibling);
        }
    } else if (card.parentNode !== contentDisplay) {
        contentDisplay.appendChild(card);
    }

    const linkAttrs = getExternalLinkAttrs();
    const renderVariantItems = (items, referenceType) => {
        return items.map((item) => {
            const variantKey = item.variant_key || '';
            const typeLabel = referenceType === 'scene_state'
                ? t('labels.variantTypeSceneState')
                : t('labels.variantTypeCharacterOutfit');
            return `
                <div class="reference-item" style="display:flex; flex-direction:column; gap:8px; padding: 12px; border: 1px solid #f0f0f0; border-radius: 10px; background: #fff; font-size: 13px; line-height: 1.5;">
                    <div style="position: relative; width: 100%; cursor: pointer; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.12);" onclick="openMediaModal('image', '${item.url}')">
                        <img src="${item.url}" alt="${escapeHtml(item.name || typeLabel)}" style="width:100%; height:180px; object-fit:cover; display:block;">
                        <div style="position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.6); color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px;">
                            ${t('labels.clickToZoom')}
                        </div>
                    </div>
                    <div style="font-size: 13px; color: #888;">${typeLabel}</div>
                    <div style="font-size: 16px; font-weight:600; color:#333; line-height: 1.4;">${escapeHtml(item.name || typeLabel)}</div>
                    <div class="item-actions" style="display:flex; gap:8px; flex-wrap: wrap;">
                        <button
                            class="item-btn regenerate reference-regenerate-btn"
                            data-locked="false"
                            data-reference-key="${escapeHtml(buildReferenceAssetKey(referenceType, variantKey))}"
                            onclick="event.stopPropagation(); regenerateVariantAsset('${referenceType}', '${encodeURIComponent(variantKey)}')"
                            style="background: #faad14; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer;"
                        >${t('actions.regenerate')}</button>
                        <a href="${item.url}" ${linkAttrs} class="item-btn download" style="background: #1890ff; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; width: fit-content;">${t('actions.download')}</a>
                    </div>
                </div>
            `;
        }).join('');
    };

    card.innerHTML = `
        <h4 style="margin: 0 0 15px 0; font-size: 18px; font-weight: 600; color: #333; border-bottom: 2px solid #1890ff; padding-bottom: 10px;">${t('labels.variantAssetsLibrary')}</h4>
        <div class="reference-image-container" style="display: flex; flex-direction: column; gap: 20px; font-size: 14px; color: #333; line-height: 1.6;">
            ${outfitImages.length > 0 ? `<div><h5 style="margin:0 0 12px 0; font-size: 16px; font-weight: 600; color: #333;">${t('labels.variantOutfits')}</h5><div class="reference-grid">${renderVariantItems(outfitImages, 'character_outfit')}</div></div>` : ''}
            ${sceneStateImages.length > 0 ? `<div><h5 style="margin:0 0 12px 0; font-size: 16px; font-weight: 600; color: #333;">${t('labels.variantSceneStates')}</h5><div class="reference-grid">${renderVariantItems(sceneStateImages, 'scene_state')}</div></div>` : ''}
        </div>
    `;

    refreshVariantAssetsActionState();
}

// 刷新“各分镜-角色装扮-布景状态”模块内重新生成按钮状态。
function refreshVariantAssetsActionState() {
    const card = document.getElementById('variant-assets-card');
    if (!card) return;
    const regenerateButtons = card.querySelectorAll('button.reference-regenerate-btn');
    regenerateButtons.forEach((regenerateBtn) => {
        const assetKey = regenerateBtn.dataset.referenceKey || '';
        const enabled = canRegenerateReferenceImage(false, assetKey);
        regenerateBtn.disabled = !enabled;
        regenerateBtn.style.opacity = enabled ? '' : '0.65';
        regenerateBtn.style.cursor = enabled ? 'pointer' : 'not-allowed';
    });
}

// 标记某个参考资产正处于“后台异步重生成”状态：保持锁定，等待 WebSocket 推送结果。
const pendingReferenceAssetRegenerations = new Set();

function markReferenceAssetRegenerationPending(referenceAssetKey) {
    if (!referenceAssetKey) return;
    pendingReferenceAssetRegenerations.add(referenceAssetKey);
    // 保持 regeneratingReferenceAssetKeys 中的锁定，不在 finally 中移除。
}

// 统一收尾：解锁按钮并刷新三个模块的状态。
// force=true 时（WebSocket 结果到达）无条件解锁；否则若该 key 仍在等待后台异步结果，则保持锁定。
function finishReferenceAssetRegeneration(referenceAssetKey, force = false) {
    if (referenceAssetKey) {
        if (!force && pendingReferenceAssetRegenerations.has(referenceAssetKey)) {
            // 仍在等待 WebSocket 推送结果，保持锁定，仅刷新状态。
            refreshReferenceImageActionState();
            refreshVariantAssetsActionState();
            refreshStoryboardActionState();
            return;
        }
        regeneratingReferenceAssetKeys.delete(referenceAssetKey);
        pendingReferenceAssetRegenerations.delete(referenceAssetKey);
    }
    referenceImageRegenerating = regeneratingReferenceAssetKeys.size > 0;
    refreshReferenceImageActionState();
    refreshVariantAssetsActionState();
    refreshStoryboardActionState();
    // 处于参考图子阶段等待（category1/category2 完成后）时，重启该子阶段倒计时；
    // 否则走主步骤（videos/merge）倒计时逻辑。
    if (pendingReferenceStage && pendingReferenceStage !== 'videos') {
        maybeStartReferenceStageCountdown();
    } else {
        maybeStartPendingStepCountdown();
    }
}

// 处理后端通过 WebSocket 推送的单张重生成结果（角色装扮图/布景状态图/故事版/参考图库）。
function handleReferenceAssetRegenerated(data) {
    data = data || {};
    const referenceAssetKey = data.reference_asset_key || '';
    if (data.success) {
        // 先结算本张（从进行中集合移除并刷新按钮），再据“是否仍有其他张在重生成”决定状态栏文案，
        // 避免在仍有排队重生成时提前显示“已完成/请确认”。
        finishReferenceAssetRegeneration(referenceAssetKey, true);
        if (data.reference_output) {
            displayReferenceImage(data.reference_output);
        }
        const name = data.reference_name || data.variant_key || '';
        if (hasPendingReferenceRegeneration()) {
            // 仍有其他图片在重新生成：保持“图片重新生成中”，不覆盖为完成态。
            renderStatusBar(t('labels.referenceImageRegeneratingText'), 'loading', t('steps.referenceImageTitle'));
        } else {
            renderStatusBar(
                t('messages.referenceAssetRegeneratedSuccess', { name }),
                'info',
                t('steps.referenceImageTitle')
            );
        }
        addAgentMessage(t('messages.referenceAssetRegeneratedSuccess', { name }));
    } else {
        const errText = data.error || '';
        addAgentMessage(t('messages.regenerateFailedWithError', { error: errText }));
        finishReferenceAssetRegeneration(referenceAssetKey, true);
    }
}

// 处理后台「分镜视频重新生成」的最终结果（经 WebSocket 推送）。
// 与同步 fetch 路径的成功/失败分支保持一致，只是触发时机改为异步。
function handleVideoSceneRegenerated(data) {
    data = data || {};
    const sceneNumber = data.scene_number;
    // 关闭该分镜的加载态（转转效果）。
    clearVideoItemLoading(sceneNumber);

    if (data.success) {
        if (data.url) {
            setVideoItemUrl(sceneNumber, data.url);
        }
        hideSkipSceneButton(sceneNumber);
        markVideoGenerated(sceneNumber);
        if (data.review) {
            displayVideoReviewResult(data.review);
        }
        renderStatusBar(t('messages.sceneVideoRegenerated', { scene: sceneNumber }), 'info', t('steps.video'));
        addAgentMessage(t('messages.sceneVideoRegenerated', { scene: sceneNumber }));
    } else {
        console.error('Regenerate failed:', data.error);
        maybeStartPendingStepCountdown();
        if (data.can_skip_scene) {
            showSkipSceneButton(sceneNumber);
            const skipMessage = data.skip_message || t('messages.skipSceneAvailable', {
                scene: sceneNumber,
                limit: data.max_generation_count || 0
            });
            renderStatusBar(skipMessage, 'info', t('steps.video'));
            addAgentMessage(skipMessage);
        } else {
            renderStatusBar(t('messages.regenerateFailedWithError', { error: data.error || t('labels.unknown') }), 'info', t('steps.video'));
            addAgentMessage(t('messages.regenerateFailedWithError', { error: data.error || t('labels.unknown') }));
        }
    }
}

// 重新生成单张角色装扮图/布景状态图
async function regenerateVariantAsset(referenceType, encodedVariantKey) {
    if (!currentProjectId) {
        alert(t('messages.createProjectFirst'));
        return;
    }
    const variantKey = decodeURIComponent(encodedVariantKey || '');
    const referenceAssetKey = buildReferenceAssetKey(referenceType, variantKey);
    if (!canRegenerateReferenceImage(false, referenceAssetKey)) {
        return;
    }

    regeneratingReferenceAssetKeys.add(referenceAssetKey);
    referenceImageRegenerating = regeneratingReferenceAssetKeys.size > 0;
    cancelAutoRunCountdown();
    refreshReferenceImageActionState();
    refreshVariantAssetsActionState();
    renderStatusBar(t('labels.referenceImageRegeneratingText'), 'loading', t('steps.referenceImageTitle'));

    try {
        const response = await fetch('/regenerate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                project_id: currentProjectId,
                type: 'image',
                scene_number: '0',
                reference_type: referenceType,
                reference_name: variantKey,
                client_id: wsClientId || '',
                ui_language: currentLanguage
            })
        });

        const result = await response.json();
        if (result.success && result.async) {
            // 后台异步生成：保持按钮锁定，结果通过 WebSocket 推送后再解锁与刷新。
            markReferenceAssetRegenerationPending(referenceAssetKey);
            return;
        }
        if (result.success) {
            displayReferenceImage(result.reference_output);
            renderStatusBar(
                t('messages.referenceAssetRegeneratedSuccess', {
                    name: result.reference_name || variantKey
                }),
                'info',
                t('steps.referenceImageTitle')
            );
            addAgentMessage(t('messages.referenceAssetRegeneratedSuccess', {
                name: result.reference_name || variantKey
            }));
        } else {
            addAgentMessage(t('messages.regenerateFailedWithError', { error: result.error }));
        }
    } catch (error) {
        console.error('Regenerate variant asset error:', error);
        addAgentMessage(t('messages.regenerateFailed'));
    } finally {
        finishReferenceAssetRegeneration(referenceAssetKey);
    }
}

// 刷新“各分镜故事版”模块内重新生成按钮状态。
function refreshStoryboardActionState() {
    const card = document.getElementById('storyboard-card');
    if (!card) return;
    const regenerateButtons = card.querySelectorAll('button.reference-regenerate-btn');
    regenerateButtons.forEach((regenerateBtn) => {
        const assetKey = regenerateBtn.dataset.referenceKey || '';
        const enabled = canRegenerateReferenceImage(false, assetKey);
        regenerateBtn.disabled = !enabled;
        regenerateBtn.style.opacity = enabled ? '' : '0.65';
        regenerateBtn.style.cursor = enabled ? 'pointer' : 'not-allowed';
    });
}

// 渲染“各分镜故事版”模块：位于布景参考图库与分镜视频之间，布局参照参考图库。
function displayStoryboards(output) {
    const storyboardImages = (output && output.storyboard_images) || [];
    const existing = document.getElementById('storyboard-card');

    if (!storyboardImages.length) {
        if (existing) existing.remove();
        return;
    }

    let card = existing;
    if (!card) {
        card = document.createElement('div');
        card.className = 'content-card';
        card.id = 'storyboard-card';
    }

    // 保证顺序：参考图库 → 角色装扮/布景状态 → 故事版 → 分镜视频。插入到装扮/状态卡片（若无则参考图库卡片）之后。
    const anchorCard = document.getElementById('variant-assets-card') || document.getElementById('reference-image-card');
    if (anchorCard && anchorCard.parentNode === contentDisplay) {
        if (anchorCard.nextSibling !== card) {
            contentDisplay.insertBefore(card, anchorCard.nextSibling);
        }
    } else if (card.parentNode !== contentDisplay) {
        contentDisplay.appendChild(card);
    }

    const linkAttrs = getExternalLinkAttrs();
    const sortedImages = [...storyboardImages].sort(
        (a, b) => (Number(a.scene_number) || 0) - (Number(b.scene_number) || 0)
    );

    const items = sortedImages.map((item) => {
        const sceneNumber = Number(item.scene_number) || 0;
        const sceneLabel = t('labels.scene', { scene: sceneNumber });
        const slotIndex = Number.isFinite(Number(item.slot_index)) ? Number(item.slot_index) : (sceneNumber - 1);
        return `
            <div class="reference-item" style="display:flex; flex-direction:column; gap:8px; padding: 12px; border: 1px solid #f0f0f0; border-radius: 10px; background: #fff; font-size: 13px; line-height: 1.5;">
                <div style="position: relative; width: 100%; cursor: pointer; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.12);" onclick="openMediaModal('image', '${item.url}')">
                    <img src="${item.url}" alt="${escapeHtml(item.name || sceneLabel)}" style="width:100%; height:180px; object-fit:cover; display:block;">
                    <div style="position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.6); color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px;">
                        ${t('labels.clickToZoom')}
                    </div>
                </div>
                <div style="font-size: 13px; color: #888;">${t('labels.storyboardImage')}</div>
                <div style="font-size: 16px; font-weight:600; color:#333; line-height: 1.4;">${escapeHtml(sceneLabel)}</div>
                <div class="item-actions" style="display:flex; gap:8px; flex-wrap: wrap;">
                    <button
                        class="item-btn regenerate reference-regenerate-btn"
                        data-locked="false"
                        data-reference-key="${escapeHtml(buildReferenceAssetKey('storyboard', sceneNumber))}"
                        onclick="event.stopPropagation(); regenerateStoryboardAsset(${sceneNumber}, ${slotIndex})"
                        style="background: #faad14; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer;"
                    >${t('actions.regenerate')}</button>
                    <a href="${item.url}" ${linkAttrs} class="item-btn download" style="background: #1890ff; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; width: fit-content;">${t('actions.download')}</a>
                </div>
            </div>
        `;
    }).join('');

    card.innerHTML = `
        <h4 style="margin: 0 0 15px 0; font-size: 18px; font-weight: 600; color: #333; border-bottom: 2px solid #1890ff; padding-bottom: 10px;">${t('labels.storyboardLibrary')}</h4>
        <div class="reference-grid">${items}</div>
    `;

    refreshStoryboardActionState();
}

function displayComicPdfLink(output) {
    const normalizedOutput = {
        status: output?.status || (output?.comic_pdf_url ? 'completed' : 'pending'),
        comic_pdf_url: output?.comic_pdf_url || '',
        error: output?.error || ''
    };
    lastComicPdfOutput = normalizedOutput;

    let card = document.getElementById('comic-pdf-card');
    if (!card) {
        card = document.createElement('div');
        card.className = 'content-card';
        card.id = 'comic-pdf-card';
    }

    const storyboardCard = document.getElementById('storyboard-card');
    const videosContainer = document.getElementById('videos-container');
    if (storyboardCard && storyboardCard.parentNode === contentDisplay) {
        if (storyboardCard.nextSibling !== card) {
            contentDisplay.insertBefore(card, storyboardCard.nextSibling);
        }
    } else if (videosContainer && videosContainer.parentNode === contentDisplay) {
        contentDisplay.insertBefore(card, videosContainer);
    } else if (card.parentNode !== contentDisplay) {
        contentDisplay.appendChild(card);
    }

    const linkAttrs = getExternalLinkAttrs();
    const isCompleted = normalizedOutput.status === 'completed' && normalizedOutput.comic_pdf_url;
    const isFailed = normalizedOutput.status === 'failed';
    const title = t('labels.comicPdfTitle');
    const statusText = isCompleted
        ? t('labels.comicPdfReady')
        : (isFailed ? t('labels.comicPdfFailed') : t('labels.comicPdfGenerating'));

    card.innerHTML = `
        <h4 style="margin: 0 0 12px 0; font-size: 18px; font-weight: 600; color: #333; border-bottom: 2px solid #1890ff; padding-bottom: 10px;">${title}</h4>
        <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">
            <div style="font-size:14px; color:${isFailed ? '#cf1322' : '#555'}; line-height:1.5;">
                ${escapeHtml(statusText)}
                ${isFailed && normalizedOutput.error ? `<div style="font-size:12px; color:#8c8c8c; margin-top:4px;">${escapeHtml(normalizedOutput.error)}</div>` : ''}
            </div>
            ${isCompleted ? `<a href="${normalizedOutput.comic_pdf_url}" ${linkAttrs} class="action-btn" style="background:#1890ff; color:white; padding:8px 14px; border-radius:6px; text-decoration:none; display:inline-flex; align-items:center; gap:5px; white-space:nowrap;">${t('actions.downloadPdf')}</a>` : ''}
        </div>
    `;
}

// 重新生成单张分镜故事版
async function regenerateStoryboardAsset(sceneNumber, slotIndex = -1) {
    if (!currentProjectId) {
        alert(t('messages.createProjectFirst'));
        return;
    }
    const referenceAssetKey = buildReferenceAssetKey('storyboard', sceneNumber);
    if (!canRegenerateReferenceImage(false, referenceAssetKey)) {
        return;
    }

    regeneratingReferenceAssetKeys.add(referenceAssetKey);
    referenceImageRegenerating = regeneratingReferenceAssetKeys.size > 0;
    cancelAutoRunCountdown();
    refreshReferenceImageActionState();
    refreshVariantAssetsActionState();
    refreshStoryboardActionState();
    renderStatusBar(t('labels.referenceImageRegeneratingText'), 'loading', t('steps.referenceImageTitle'));

    try {
        const response = await fetch('/regenerate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                project_id: currentProjectId,
                type: 'image',
                scene_number: '0',
                reference_type: 'storyboard',
                reference_name: String(sceneNumber),
                reference_slot_index: String(Number.isFinite(Number(slotIndex)) ? Number(slotIndex) : -1),
                client_id: wsClientId || '',
                ui_language: currentLanguage
            })
        });

        const result = await response.json();
        if (result.success && result.async) {
            // 后台异步生成：保持按钮锁定，结果通过 WebSocket 推送后再解锁与刷新。
            markReferenceAssetRegenerationPending(referenceAssetKey);
            return;
        }
        if (result.success) {
            displayReferenceImage(result.reference_output);
            renderStatusBar(
                t('messages.storyboardRegeneratedSuccess', { scene: sceneNumber }),
                'info',
                t('steps.referenceImageTitle')
            );
            addAgentMessage(t('messages.storyboardRegeneratedSuccess', { scene: sceneNumber }));
        } else {
            addAgentMessage(t('messages.regenerateFailedWithError', { error: result.error }));
        }
    } catch (error) {
        console.error('Regenerate storyboard asset error:', error);
        addAgentMessage(t('messages.regenerateFailed'));
    } finally {
        finishReferenceAssetRegeneration(referenceAssetKey);
    }
}

// 重新生成单张参考图
async function regenerateReferenceAsset(referenceType, encodedReferenceName, referenceSlotIndex = -1) {
    if (!currentProjectId) {
        alert(t('messages.createProjectFirst'));
        return;
    }
    if (!canRegenerateReferenceImage(false)) {
        return;
    }

    const referenceName = decodeURIComponent(encodedReferenceName || '');
    const referenceAssetKey = buildReferenceAssetKey(referenceType, referenceName);
    const card = document.getElementById('reference-image-card');
    if (!card) return;

    regeneratingReferenceAssetKeys.add(referenceAssetKey);
    referenceImageRegenerating = regeneratingReferenceAssetKeys.size > 0;
    cancelAutoRunCountdown();
    refreshReferenceImageActionState();
    renderStatusBar(t('labels.referenceImageRegeneratingText'), 'loading', t('steps.referenceImageTitle'));

    try {
        const response = await fetch('/regenerate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                project_id: currentProjectId,
                type: 'image',
                scene_number: '0',
                reference_type: referenceType,
                reference_name: referenceName,
                reference_slot_index: String(Number.isFinite(Number(referenceSlotIndex)) ? Number(referenceSlotIndex) : -1),
                client_id: wsClientId || '',
                ui_language: currentLanguage
            })
        });

        const result = await response.json();
        if (result.success && result.async) {
            // 后台异步生成：保持按钮锁定，结果通过 WebSocket 推送后再解锁与刷新。
            markReferenceAssetRegenerationPending(referenceAssetKey);
            return;
        }
        if (result.success) {
            displayReferenceImage(result.reference_output);
            renderStatusBar(
                t('messages.referenceAssetRegeneratedSuccess', {
                    name: result.reference_name || referenceName
                }),
                'info',
                t('steps.referenceImageTitle')
            );
            addAgentMessage(t('messages.referenceAssetRegeneratedSuccess', {
                name: result.reference_name || referenceName
            }));
        } else {
            addAgentMessage(t('messages.regenerateFailedWithError', { error: result.error }));
        }
    } catch (error) {
        console.error('Regenerate reference asset error:', error);
        addAgentMessage(t('messages.regenerateFailed'));
    } finally {
        finishReferenceAssetRegeneration(referenceAssetKey);
    }
}

// 显示所有图片（参考图库 + 分镜 + 结尾帧）
function displayImages(output) {
    lastImagesOutput = output;
    lastReferenceImageOutput = null;
    // 更新当前步骤为 images（确保后续流程正确）
    currentStep = 'images';

    // 高亮分镜图步骤
    updateStepHighlight('image_agent', 100, 'scenes');

    // 移除参考图卡片（如果存在）
    const refCard = document.getElementById('reference-image-card');
    if (refCard) {
        refCard.remove();
    }

    // 检查是否已存在图片卡片
    const existingCard = document.getElementById('images-card');
    if (existingCard) {
        // 如果已经存在，检查是否已经有视频容器（避免删除已生成的视频）
        const existingVideosContainer = document.getElementById('videos-container');
        if (existingVideosContainer) {
            // 将视频容器移到 contentDisplay，避免被删除
            contentDisplay.appendChild(existingVideosContainer);
        }
        // 现在安全地删除旧的图片卡片
        existingCard.remove();
    }

    const card = document.createElement('div');
    card.className = 'content-card';
    card.id = 'images-card';
    card.innerHTML = `<h4>${t('labels.generatedImages')}</h4>`;
    const linkAttrs = getExternalLinkAttrs();

    const grid = document.createElement('div');
    grid.className = 'images-grid';
    grid.id = 'images-grid';

    // 新的图片结构: 参考图库 + 分镜1-N + 结尾帧
    // 假设 output.images 包含完整的图片信息
    const images = output.images || output.urls.map((url, idx) => ({
        url: url,
        scene_number: idx === output.urls.length - 1 ? 999 : (idx === 0 ? 0 : idx),
        is_end_frame: idx === output.urls.length - 1,
        is_reference: idx === 0
    }));

    images.forEach((img) => {
        const sceneNum = img.scene_number;
        const isEndFrame = img.is_end_frame;
        const isReference = img.is_reference;

        let label;
        if (isReference) {
            label = t('labels.referenceImage');
        } else if (isEndFrame || sceneNum === 999) {
            label = t('labels.endFrame');
        } else {
            label = t('labels.scene', { scene: sceneNum });
        }

        const item = document.createElement('div');
        item.className = 'image-item';
        item.id = `image-item-${sceneNum}`;
        item.innerHTML = `
            <img src="${img.url}" alt="${label}" class="clickable-media" onclick="openMediaModal('image', '${img.url}')">
            <div class="image-label">${label}</div>
            <div class="item-actions" style="display: flex; gap: 8px; flex-wrap: nowrap; white-space: nowrap;">
                <button class="item-btn regenerate" onclick="regenerateImage(${sceneNum})" style="white-space: nowrap; flex-shrink: 0; padding: 6px 12px; font-size: 12px;">${t('actions.regenerate')}</button>
                <a href="${img.url}" ${linkAttrs} class="item-btn download" style="white-space: nowrap; flex-shrink: 0; padding: 6px 12px; font-size: 12px;">${t('actions.download')}</a>
            </div>
        `;
        grid.appendChild(item);
    });

    card.appendChild(grid);
    contentDisplay.appendChild(card);
    contentDisplay.scrollTop = contentDisplay.scrollHeight;
}

// 重新生成图片
async function regenerateImage(sceneNumber) {
    if (!currentProjectId) {
        alert(t('messages.createProjectFirst'));
        return;
    }

    const imageItem = document.getElementById(`image-item-${sceneNumber}`);
    if (!imageItem) return;

    // 确定标签文本
    let label;
    if (sceneNumber === 0) {
        label = t('labels.referenceImage');
    } else if (sceneNumber === 999) {
        label = t('labels.endFrame');
    } else {
        label = t('labels.scene', { scene: sceneNumber });
    }

    const originalContent = imageItem.innerHTML;
    imageItem.innerHTML = `
        <div class="item-generating-overlay">
            <div class="item-generating-spinner"></div>
            <div class="item-generating-text">${t('status.generating')}</div>
        </div>
    `;

    try {
        // 发送重新生成请求
        const response = await fetch('/regenerate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                project_id: currentProjectId,
                type: 'image',
                scene_number: sceneNumber.toString(),
                client_id: wsClientId || '',
                ui_language: currentLanguage
            })
        });

        const result = await response.json();

        if (result.success) {
            const linkAttrs = getExternalLinkAttrs();
            // 更新图片显示，保持可点击放大
            imageItem.innerHTML = `
                <img src="${result.url}" alt="${label}" class="clickable-media" onclick="openMediaModal('image', '${result.url}')">
                <div class="image-label">${label}</div>
                <div class="item-actions" style="display: flex; gap: 8px; flex-wrap: nowrap; white-space: nowrap;">
                    <button class="item-btn regenerate" onclick="regenerateImage(${sceneNumber})" style="white-space: nowrap; flex-shrink: 0; padding: 6px 12px; font-size: 12px;">${t('actions.regenerate')}</button>
                    <a href="${result.url}" ${linkAttrs} class="item-btn download" style="white-space: nowrap; flex-shrink: 0; padding: 6px 12px; font-size: 12px;">${t('actions.download')}</a>
                </div>
            `;
            addAgentMessage(t('messages.itemRegeneratedSuccess', { label }));
        } else {
            // 恢复原始内容
            imageItem.innerHTML = originalContent;
            addAgentMessage(t('messages.regenerateFailedWithError', { error: result.error }));
        }
    } catch (error) {
        console.error('Regenerate image error:', error);
        // 恢复原始内容
        imageItem.innerHTML = originalContent;
        addAgentMessage(t('messages.regenerateFailed'));
    }
}

// 显示视频
function displayVideos(output) {
    if (output.status === 'scene_skipped') {
        handleSceneSkipped(output);
        return;
    }
    if (output.scene_number) {
        videoOutputsByScene[output.scene_number] = { ...output };
    }
    // 更新当前步骤为 videos（确保后续流程正确）
    referenceImageLocked = true;
    currentStep = 'videos';
    refreshAllReferenceActionStates();
    ensureVideosContainer();

    // 兼容两种输出：
    // 1) 单个分镜（新流程逐个生成）：{scene_number, url, status, total_scenes, ...}
    // 2) 批量（旧流程）：{videos:[{scene_number,url}], urls:[...]}
    const totalScenes = output.total_scenes || null;
    if (totalScenes) videoTotalScenes = totalScenes;

    const updates = [];
    if (output.scene_number) {
        updates.push({ scene_number: output.scene_number, url: output.url, status: output.status, message: output.message });
    } else if (output.videos) {
        output.videos.forEach(v => updates.push({ scene_number: v.scene_number, url: v.url, status: 'generated' }));
    } else if (output.urls) {
        output.urls.forEach((url, idx) => updates.push({ scene_number: idx + 1, url, status: 'generated' }));
    }

    updates.forEach((u) => {
        const sceneNum = u.scene_number;
        ensureVideoItem(sceneNum);

        if (u.status === 'generating') {
            hideSkipSceneButton(sceneNum);
            setVideoItemLoading(sceneNum, t('status.sceneGenerating', { scene: sceneNum }));
            renderStatusBar(t('status.sceneGenerating', { scene: sceneNum }), 'loading', t('steps.video'));
            return;
        }

        if (u.status === 'regenerating') {
            hideSkipSceneButton(sceneNum);
            setVideoItemLoading(sceneNum, t('status.sceneRegenerating', { scene: sceneNum }));
            renderStatusBar(t('status.sceneRegenerating', { scene: sceneNum }), 'loading', t('steps.video'));
            return;
        }

        if (u.status === 'duplicate_seed') {
            hideSkipSceneButton(sceneNum);
            const duplicateSeedStatus = t('status.duplicateSeedRetrying', {
                scene: sceneNum,
                seed: u.seed || t('labels.unknown')
            });
            const duplicateSeedMessage = t('messages.duplicateSeedRetry', {
                scene: sceneNum,
                seed: u.seed || t('labels.unknown')
            });
            setVideoItemLoading(sceneNum, duplicateSeedStatus);
            renderStatusBar(duplicateSeedStatus, 'loading', t('steps.video'));
            addAgentMessage(duplicateSeedMessage);
            return;
        }

        // 只要有 URL 就立即显示视频，不管 status 是什么（包括 'generated' 或 undefined）
        if (u.url && u.url.trim() !== '') {
            setVideoItemUrl(sceneNum, u.url);
            clearVideoItemLoading(sceneNum);
            hideSkipSceneButton(sceneNum);
            markVideoGenerated(sceneNum);

            // 添加成功提示消息到聊天
            addAgentMessage(t('messages.sceneVideoGenerated', { scene: sceneNum }));
        }
    });

    updateVideoStepProgressUI();

    contentDisplay.scrollTop = contentDisplay.scrollHeight;
}

// 打开视频弹窗（点击后放大播放）
function openVideoModal(url) {
    if (!url) return;
    mediaModalBody.innerHTML = '';

    const video = document.createElement('video');
    video.src = url;
    video.controls = true;
    video.autoplay = true; // 在弹窗中自动播放
    video.style.maxWidth = '90vw';
    video.style.maxHeight = '90vh';
    video.style.width = '100%';
    video.style.height = 'auto';
    mediaModalBody.appendChild(video);

    mediaModal.classList.add('active');
    document.body.style.overflow = 'hidden'; // 防止背景滚动
}

// 关闭媒体弹窗（停止播放视频）
function closeMediaModal() {
    mediaModal.classList.remove('active');
    // 停止所有视频播放
    const videos = mediaModalBody.querySelectorAll('video');
    videos.forEach(video => {
        video.pause();
        video.src = ''; // 清空src确保停止加载
    });
    mediaModalBody.innerHTML = ''; // 清空内容
    document.body.style.overflow = ''; // 恢复背景滚动
}

// 重新生成视频
async function regenerateVideo(sceneNumber) {
    if (!currentProjectId) {
        alert(t('messages.createProjectFirst'));
        return;
    }

    const videoItem = document.getElementById(`video-item-${sceneNumber}`);
    if (!videoItem) {
        console.error(`Video item not found: video-item-${sceneNumber}`);
        return;
    }

    cancelAutoRunCountdown();
    // 在该分镜视频上显示转转效果（不破坏原DOM结构）
    setVideoItemLoading(sceneNumber, t('status.sceneRegenerating', { scene: sceneNumber }));
    renderStatusBar(t('status.sceneRegenerating', { scene: sceneNumber }), 'loading', t('steps.video'));
    addAgentMessage(t('status.sceneRegenerating', { scene: sceneNumber }));

    try {
        // 发送重新生成请求
        const response = await fetch('/regenerate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                project_id: currentProjectId,
                type: 'video',
                scene_number: sceneNumber.toString(),
                client_id: wsClientId || '',
                ui_language: currentLanguage
            })
        });

        if (!response.ok) {
            throw new Error(getHttpErrorMessage(response.status));
        }

        const result = await response.json();

        if (result.success && result.async) {
            // 后台异步生成：视频重生成+审核耗时可达数分钟，超过云端网关超时。
            // 保持该分镜的加载态，最终结果通过 WebSocket（video_scene_regenerated）推送后再更新。
            return;
        }

        if (result.success) {
            // 更新视频显示并关闭转转效果
            setVideoItemUrl(sceneNumber, result.url);
            clearVideoItemLoading(sceneNumber);
            hideSkipSceneButton(sceneNumber);
            markVideoGenerated(sceneNumber);
            if (result.review) {
                displayVideoReviewResult(result.review);
            }
            renderStatusBar(t('messages.sceneVideoRegenerated', { scene: sceneNumber }), 'info', t('steps.video'));
            addAgentMessage(t('messages.sceneVideoRegenerated', { scene: sceneNumber }));
        } else {
            console.error('Regenerate failed:', result.error);
            clearVideoItemLoading(sceneNumber);
            maybeStartPendingStepCountdown();
            if (result.can_skip_scene) {
                showSkipSceneButton(sceneNumber);
                const skipMessage = result.skip_message || t('messages.skipSceneAvailable', {
                    scene: sceneNumber,
                    limit: result.max_generation_count || 0
                });
                renderStatusBar(skipMessage, 'info', t('steps.video'));
                addAgentMessage(skipMessage);
            } else {
                renderStatusBar(t('messages.regenerateFailedWithError', { error: result.error || t('labels.unknown') }), 'info', t('steps.video'));
                addAgentMessage(t('messages.regenerateFailedWithError', { error: result.error || t('labels.unknown') }));
            }
        }
    } catch (error) {
        console.error('Regenerate video error:', error);
        clearVideoItemLoading(sceneNumber);
        maybeStartPendingStepCountdown();
        renderStatusBar(t('messages.regenerateFailedWithError', { error: error.message || t('labels.unknown') }), 'info', t('steps.video'));
        addAgentMessage(t('messages.regenerateFailedWithError', { error: error.message || t('labels.unknown') }));
    }
}

async function skipScene(sceneNumber) {
    if (!currentProjectId) {
        alert(t('messages.createProjectFirst'));
        return;
    }

    setVideoItemLoading(sceneNumber, t('status.skippingScene', { scene: sceneNumber }));
    renderStatusBar(t('status.skippingScene', { scene: sceneNumber }), 'loading', t('steps.video'));

    try {
        const response = await fetch('/skip_scene', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                project_id: currentProjectId,
                scene_number: sceneNumber.toString(),
                client_id: wsClientId || '',
                ui_language: currentLanguage
            })
        });

        if (!response.ok) {
            throw new Error(getHttpErrorMessage(response.status));
        }

        const result = await response.json();
        if (!result.success) {
            clearVideoItemLoading(sceneNumber);
            renderStatusBar(t('messages.skipSceneFailed', { error: result.error || t('labels.unknown') }), 'info', t('steps.video'));
            addAgentMessage(t('messages.skipSceneFailed', { error: result.error || t('labels.unknown') }));
        }
    } catch (error) {
        console.error('Skip scene error:', error);
        clearVideoItemLoading(sceneNumber);
        renderStatusBar(t('messages.skipSceneFailed', { error: error.message || t('labels.unknown') }), 'info', t('steps.video'));
        addAgentMessage(t('messages.skipSceneFailed', { error: error.message || t('labels.unknown') }));
    }
}

// 显示合成步骤（转转效果）
function showMergeStep() {
    mergeStepVisible = true;
    referenceImageLocked = true;
    refreshAllReferenceActionStates();
    // 检查是否已存在合成步骤卡片
    let mergeCard = document.getElementById('merge-step-card');
    if (mergeCard) {
        mergeCard.remove();
    }

    // 创建合成步骤卡片
    mergeCard = document.createElement('div');
    mergeCard.className = 'content-card';
    mergeCard.id = 'merge-step-card';
    mergeCard.innerHTML = `
        <h4 style="margin: 0 0 15px 0; color: #1890ff; border-bottom: 2px solid #1890ff; padding-bottom: 10px;">
            ${t('labels.mergeTitle')}
        </h4>
    `;

    // 添加到内容展示区
    contentDisplay.appendChild(mergeCard);
    contentDisplay.scrollTop = contentDisplay.scrollHeight;

    // 更新右上角进度
    stepProgress.merge = 50;
    updateStepHighlight('merge_agent', 50);
    updateOverallProgress();
    renderStatusBar(t('loading.merge'), 'loading', t('steps.mergeTitle'));
}

// 显示最终视频
function displayFinalVideo(url) {
    lastFinalVideoUrl = url;
    mergeStepVisible = false;
    hideFullscreenGenerating();
    hideStatusSection();

    // 移除合成步骤卡片
    const mergeCard = document.getElementById('merge-step-card');
    if (mergeCard) {
        mergeCard.remove();
    }

    // 将最终视频添加到内容展示区
    const linkAttrs = getExternalLinkAttrs();
    const card = document.createElement('div');
    card.className = 'content-card';
    card.id = 'final-video-card';
    card.innerHTML = `
        <h4 style="margin: 0 0 15px 0; color: #52c41a; border-bottom: 2px solid #52c41a; padding-bottom: 10px;">
            ${t('labels.finalVideoTitle')}
        </h4>
        <div style="display: flex; flex-direction: column; align-items: center; gap: 15px;">
            <div style="position: relative; width: 100%; max-width: 400px; cursor: pointer; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.15);" onclick="openMediaModal('video', '${url}')">
                <video muted playsinline preload="metadata" tabindex="-1" style="width: 100%; display: block; border-radius: 8px; pointer-events: none;">
                    <source src="${url}" type="video/mp4">
                    ${t('labels.browserNoVideo')}
                </video>
                <div style="position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,0.6); color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px;">
                    ${t('labels.clickToZoom')}
                </div>
            </div>
            <div class="video-actions" style="display: flex; gap: 10px; flex-wrap: nowrap;">
                <a href="${url}" ${linkAttrs} class="action-btn" style="background: #1890ff; color: white; padding: 10px 20px; border-radius: 6px; text-decoration: none; display: inline-flex; align-items: center; gap: 5px;">
                    ${t('actions.downloadVideo')}
                </a>
                <button class="action-btn" id="newProjectBtn" onclick="resetProject()" style="background: #52c41a; color: white; padding: 10px 20px; border-radius: 6px; border: none; cursor: pointer; display: inline-flex; align-items: center; gap: 5px;">
                    ${t('actions.newProject')}
                </button>
            </div>
        </div>
    `;

    // 添加到内容展示区
    contentDisplay.appendChild(card);
    contentDisplay.scrollTop = contentDisplay.scrollHeight;

    // 更新右上角进度为完成
    stepProgress.merge = 100;
    stepProgress.videos = 100;
    updateStepHighlight('merge_agent', 100);
    updateOverallProgress();
    updateProjectActionState();
}

// 显示加载
function showLoading(text = t('status.processing')) {
    renderStatusBar(text, 'loading');
}

// 隐藏加载
function hideLoading() {
    if (statusSection && statusSection.dataset.mode === 'loading') {
        hideStatusSection();
    }
}

// 重置项目
function resetProject() {
    currentProjectId = null;
    hasDraftProject = false;
    persistActiveProject(null); // 清除刷新恢复标记：新建/结束后不再恢复旧项目
    uploadedImages = [];
    uploadedAudio = null;
    currentStep = null;
    isWaitingForConfirm = false;
    pendingStep = null;
    referenceStage = null;
    pendingReferenceStage = null;
    referenceStageHasCategory2 = false;
    overallProgress = 0;
    stepProgress = { script: 0, reference_image: 0, videos: 0, merge: 0 };
    isAutoRunMode = false; // 重置全自动模式
    lastScriptData = null;
    lastReferenceImageOutput = null;
    lastImagesOutput = null;
    lastFinalVideoUrl = null;
    referenceImageLocked = false;
    referenceImageRegenerateLocked = false;
    referenceImageRegenerating = false;
    projectEnding = false;
    projectEnded = false;
    projectEndBeaconSent = false;
    cancelAutoRunCountdown();
    mergeStepVisible = false;
    videoOutputsByScene = {};
    videoReviewOutputsByScene = {};
    videoTotalScenes = null;
    videoSceneState = {};

    renderWelcomeMessage(true);
    contentDisplay.innerHTML = `
        <div class="empty-state" id="emptyState">
            <div class="empty-icon">🎥</div>
            <p id="emptyStateText">${t('progress.emptyState')}</p>
        </div>
    `;
    
    // 重置进度条
    const progressFill = document.getElementById('overallProgressFill');
    const progressPercent = document.getElementById('overallProgressPercent');
    if (progressFill) progressFill.style.width = '0%';
    if (progressPercent) progressPercent.textContent = '0%';
    
    // 重置步骤高亮
    document.querySelectorAll('.step-progress-item').forEach(step => {
        step.classList.remove('active', 'completed');
    });
    
    hideStatusSection();
    updateUploadedFiles();
    updateProjectActionState();
}

// HTML 转义
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 启动应用
document.addEventListener('DOMContentLoaded', init);
