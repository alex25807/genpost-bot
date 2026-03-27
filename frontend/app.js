const statusSummaryEl = document.getElementById("status-summary");
const serviceStatusesEl = document.getElementById("service-statuses");
const refreshStatusBtn = document.getElementById("refresh-status-btn");
const publishResultsEl = document.getElementById("publish-results");
const previewHintEl = document.getElementById("preview-hint");
const logEl = document.getElementById("log");
const businessContextEl = document.getElementById("business-context");
const goalEl = document.getElementById("goal");
const consultantMessageEl = document.getElementById("consultant-message");
const topicSuggestionsEl = document.getElementById("topic-suggestions");
const topicEl = document.getElementById("topic");
const toneEl = document.getElementById("tone");
const includeImageEl = document.getElementById("include-image");
const imagePreferencesEl = document.getElementById("image-preferences");
const imageHintBtn = document.getElementById("image-hint-btn");
const contentEl = document.getElementById("content");
const imagePromptEl = document.getElementById("image-prompt");
const imageUrlEl = document.getElementById("image-url");
const imagePreviewEl = document.getElementById("image-preview");
const suggestTopicsBtn = document.getElementById("suggest-topics-btn");
const generateBtn = document.getElementById("generate-btn");
const regenerateImageBtn = document.getElementById("regenerate-image-btn");
const publishBtn = document.getElementById("publish-btn");

let serviceStatus = {
    openai_ready: false,
    vk_ready: false,
    telegram_ready: false,
};

let suggestedTopics = [];
let selectedSuggestedTopic = "";

function selectedPlatforms() {
    return Array.from(document.querySelectorAll("input[name='platform']:checked"))
        .map((input) => input.value);
}

function setLog(message) {
    logEl.textContent = message;
}

function setConsultantMessage(message) {
    consultantMessageEl.textContent = message;
}

function humanizeFieldName(fieldName) {
    const labels = {
        topic: "Тема",
        tone: "Тон",
        platforms: "Площадки",
        content: "Текст поста",
        image_preferences: "Параметры изображения",
        image_url: "Ссылка на изображение",
    };
    return labels[fieldName] || fieldName || "Поле";
}

function formatApiError(data, fallbackMessage) {
    if (!data) {
        return fallbackMessage;
    }

    if (typeof data.detail === "string" && data.detail.trim()) {
        return data.detail.trim();
    }

    if (Array.isArray(data.detail) && data.detail.length) {
        return data.detail.map((item) => {
            const fieldPath = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : "";
            const message = item.msg || "Некорректное значение.";
            return `${humanizeFieldName(fieldPath)}: ${message}`;
        }).join(" | ");
    }

    if (typeof data.error === "string" && data.error.trim()) {
        return data.error.trim();
    }

    return fallbackMessage;
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
}

function platformLabel(platform) {
    return platform === "vk" ? "VK" : "Telegram";
}

function escapeAttribute(value) {
    return escapeHtml(value).replaceAll("\"", "&quot;");
}

function selectSuggestedTopic(topic) {
    selectedSuggestedTopic = topic;
    topicEl.value = topic;
    renderTopicSuggestions();
    setLog(`Тема выбрана: ${topic}`);
}

function renderTopicSuggestions() {
    if (!suggestedTopics.length) {
        topicSuggestionsEl.innerHTML = `
            <div class="topic-suggestion-empty">Здесь появятся темы, которые клиент сможет выбрать одним кликом.</div>
        `;
        return;
    }

    topicSuggestionsEl.innerHTML = suggestedTopics.map((topic, index) => `
        <button
            type="button"
            class="topic-suggestion-card ${selectedSuggestedTopic === topic ? "selected" : ""}"
            data-topic="${escapeAttribute(topic)}"
        >
            <h3>Тема ${index + 1}</h3>
            <p>${escapeHtml(topic)}</p>
        </button>
    `).join("");

    topicSuggestionsEl.querySelectorAll("[data-topic]").forEach((button) => {
        button.addEventListener("click", () => {
            selectSuggestedTopic(button.dataset.topic || "");
        });
    });
}

function renderServiceCards(data) {
    const cards = [
        {
            title: "OpenAI",
            ready: data.openai_ready,
            readyText: "Генерация доступна",
            notReadyText: "Проверьте OPENAI_API_KEY и доступность OpenAI",
        },
        {
            title: "VK",
            ready: data.vk_ready,
            readyText: "Публикация в VK доступна",
            notReadyText: "Проверьте VK_API_KEY, VK_GROUP_ID и доступ к VK API",
        },
        {
            title: "Telegram",
            ready: data.telegram_ready,
            readyText: "Публикация в Telegram доступна",
            notReadyText: "Проверьте TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID и доступ бота",
        },
    ];

    serviceStatusesEl.innerHTML = cards.map((card) => `
        <article class="service-card ${card.ready ? "service-card-ready" : "service-card-error"}">
            <h3>${card.title}</h3>
            <p class="status-chip">${card.ready ? "Готов" : "Недоступен"}</p>
            <p>${card.ready ? card.readyText : card.notReadyText}</p>
        </article>
    `).join("");
}

function renderServiceError(message) {
    statusSummaryEl.textContent = message;
    serviceStatusesEl.innerHTML = `
        <article class="service-card service-card-error">
            <h3>Статус сервисов</h3>
            <p class="status-chip">Ошибка</p>
            <p>${escapeHtml(message)}</p>
        </article>
    `;
}

function renderStatusSummary(data) {
    const readyPlatforms = [];
    if (data.vk_ready) {
        readyPlatforms.push("VK");
    }
    if (data.telegram_ready) {
        readyPlatforms.push("Telegram");
    }

    if (!data.openai_ready) {
        statusSummaryEl.textContent = "OpenAI недоступен: генерация сейчас не сработает.";
        return;
    }

    if (!readyPlatforms.length) {
        statusSummaryEl.textContent = "Генерация доступна, но платформы публикации пока не настроены.";
        return;
    }

    statusSummaryEl.textContent = `Генерация доступна. Публикация готова для: ${readyPlatforms.join(", ")}.`;
}

function renderPublishPlaceholder() {
    publishResultsEl.innerHTML = `
        <article class="publish-card publish-card-idle">
            <h3>VK</h3>
            <p class="status-chip">Ожидание</p>
            <p>Публикация еще не запускалась.</p>
        </article>
        <article class="publish-card publish-card-idle">
            <h3>Telegram</h3>
            <p class="status-chip">Ожидание</p>
            <p>Публикация еще не запускалась.</p>
        </article>
    `;
}

function renderPublishPending(platforms) {
    publishResultsEl.innerHTML = ["vk", "telegram"].map((platform) => {
        const selected = platforms.includes(platform);
        return `
            <article class="publish-card ${selected ? "publish-card-pending" : "publish-card-idle"}">
                <h3>${platformLabel(platform)}</h3>
                <p class="status-chip">${selected ? "В работе" : "Не выбрано"}</p>
                <p>${selected ? "Отправляю запрос на публикацию..." : "Платформа не выбрана для этого запуска."}</p>
            </article>
        `;
    }).join("");
}

function renderPublishRequestError(platforms, message) {
    publishResultsEl.innerHTML = ["vk", "telegram"].map((platform) => {
        const selected = platforms.includes(platform);
        return `
            <article class="publish-card ${selected ? "publish-card-error" : "publish-card-idle"}">
                <h3>${platformLabel(platform)}</h3>
                <p class="status-chip">${selected ? "Ошибка" : "Не выбрано"}</p>
                <p>${selected ? escapeHtml(message) : "Платформа не участвовала в публикации."}</p>
            </article>
        `;
    }).join("");
}

function renderPublishResults(results, selected) {
    const resultByPlatform = Object.fromEntries(results.map((item) => [item.platform, item]));

    publishResultsEl.innerHTML = ["vk", "telegram"].map((platform) => {
        const result = resultByPlatform[platform];
        const chosen = selected.includes(platform);

        if (!chosen) {
            return `
                <article class="publish-card publish-card-idle">
                    <h3>${platformLabel(platform)}</h3>
                    <p class="status-chip">Не выбрано</p>
                    <p>Платформа не участвовала в публикации.</p>
                </article>
            `;
        }

        if (!result) {
            return `
                <article class="publish-card publish-card-error">
                    <h3>${platformLabel(platform)}</h3>
                    <p class="status-chip">Ошибка</p>
                    <p>Backend не вернул результат для этой платформы.</p>
                </article>
            `;
        }

        const details = result.details && Object.keys(result.details).length
            ? `<pre>${escapeHtml(JSON.stringify(result.details, null, 2))}</pre>`
            : "<p>Дополнительных деталей нет.</p>";

        return `
            <article class="publish-card ${result.success ? "publish-card-success" : "publish-card-error"}">
                <h3>${platformLabel(platform)}</h3>
                <p class="status-chip">${result.success ? "Успех" : "Ошибка"}</p>
                <p>${result.success ? "Публикация завершилась успешно." : escapeHtml(result.details?.error || "Платформа вернула ошибку.")}</p>
                ${details}
            </article>
        `;
    }).join("");
}

function updatePreview(url) {
    if (url) {
        imagePreviewEl.src = url;
        imagePreviewEl.hidden = false;
        previewHintEl.textContent = "Изображение готово к публикации";
        return;
    }

    imagePreviewEl.hidden = true;
    imagePreviewEl.removeAttribute("src");
    previewHintEl.textContent = "Пока нет изображения";
}

async function loadStatus() {
    refreshStatusBtn.disabled = true;

    try {
        const response = await fetch("/api/status");
        const data = await response.json();
        if (!response.ok) {
            throw new Error(formatApiError(data, "Не удалось получить статус сервисов."));
        }

        serviceStatus = data;
        renderStatusSummary(data);
        renderServiceCards(data);
    } catch (error) {
        renderServiceError(error.message);
        throw error;
    } finally {
        refreshStatusBtn.disabled = false;
    }
}

function updatePublishAvailability() {
    publishBtn.disabled = !contentEl.value.trim();
}

function updateImageAvailability() {
    regenerateImageBtn.disabled = !contentEl.value.trim() || !topicEl.value.trim();
    imageHintBtn.disabled = !topicEl.value.trim();
}

async function showImagePromptHint() {
    if (!topicEl.value.trim()) {
        setLog("Сначала укажите тему поста, чтобы показать пример промпта.");
        topicEl.focus();
        return;
    }

    imageHintBtn.disabled = true;
    try {
        const query = encodeURIComponent(topicEl.value.trim());
        const response = await fetch(`/api/image-prompt-hint?topic=${query}`);
        const data = await response.json();
        if (!response.ok) {
            throw new Error(formatApiError(data, "Не удалось получить пример промпта."));
        }

        const hint = typeof data.hint === "string" && data.hint.trim()
            ? data.hint.trim()
            : "Пример промпта пока недоступен.";
        window.alert(hint);
        setLog("Показал пример промпта для текущей темы.");
    } catch (error) {
        setLog(error.message);
    } finally {
        updateImageAvailability();
    }
}

async function suggestTopics() {
    if (!businessContextEl.value.trim()) {
        setLog("Опишите нишу, продукт или аудиторию клиента.");
        businessContextEl.focus();
        return;
    }

    suggestTopicsBtn.disabled = true;
    setConsultantMessage("AI-консультант подбирает темы...");
    setLog("Подбираю темы для клиента...");

    try {
        const response = await fetch("/api/topic-suggestions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                business_context: businessContextEl.value.trim(),
                goal: goalEl.value.trim() || "получить идеи для полезных и продающих постов",
                tone: toneEl.value.trim() || "экспертный и дружелюбный",
                count: 5,
            }),
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(formatApiError(data, "Не удалось подобрать темы для клиента."));
        }

        suggestedTopics = Array.isArray(data.topics) ? data.topics : [];
        selectedSuggestedTopic = "";
        setConsultantMessage(data.consultant_message || "Вот несколько тем, которые можно предложить клиенту.");
        renderTopicSuggestions();

        if (suggestedTopics.length) {
            selectSuggestedTopic(suggestedTopics[0]);
            setLog("Темы подобраны. Выбери подходящую тему и запускай генерацию поста.");
        } else {
            setLog("AI не вернул темы для выбора. Попробуйте уточнить нишу клиента.");
        }
    } catch (error) {
        suggestedTopics = [];
        selectedSuggestedTopic = "";
        renderTopicSuggestions();
        setConsultantMessage("Не удалось получить темы. Уточните запрос и попробуйте снова.");
        setLog(error.message);
    } finally {
        suggestTopicsBtn.disabled = false;
    }
}

function buildGenerateLog(data) {
    const notes = [
        `Текст готов для платформ: ${data.platforms.map(platformLabel).join(", ")}.`,
    ];

    if (data.image_url) {
        notes.push("Изображение сгенерировано, можно посмотреть превью и при желании заменить ссылку.");
    } else {
        notes.push("Контент сгенерирован без изображения. Публикация текста доступна.");
    }

    const unavailable = [];
    if (data.platforms.includes("vk") && !serviceStatus.vk_ready) {
        unavailable.push("VK");
    }
    if (data.platforms.includes("telegram") && !serviceStatus.telegram_ready) {
        unavailable.push("Telegram");
    }
    if (unavailable.length) {
        notes.push(`Внимание: сейчас не настроены сервисы публикации для ${unavailable.join(", ")}.`);
    }

    return notes.join(" ");
}

function summarizePublishResults(results) {
    const success = results.filter((item) => item.success).map((item) => platformLabel(item.platform));
    const failed = results.filter((item) => !item.success).map((item) => platformLabel(item.platform));

    if (success.length && failed.length) {
        return `Частичный успех. Опубликовано: ${success.join(", ")}. С ошибкой: ${failed.join(", ")}.`;
    }

    if (success.length) {
        return `Публикация завершилась успешно: ${success.join(", ")}.`;
    }

    return `Публикация не удалась: ${failed.join(", ")}.`;
}

async function generatePost() {
    const platforms = selectedPlatforms();
    if (!topicEl.value.trim()) {
        setLog("Укажите тему поста.");
        return;
    }
    if (!platforms.length) {
        setLog("Выберите хотя бы одну площадку.");
        return;
    }

    generateBtn.disabled = true;
    publishBtn.disabled = true;
    setLog(includeImageEl.checked ? "Генерирую текст, промпт и изображение..." : "Генерирую текст поста...");

    try {
        const response = await fetch("/api/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                topic: topicEl.value.trim(),
                tone: toneEl.value.trim(),
                include_image: includeImageEl.checked,
                image_preferences: imagePreferencesEl.value.trim() || null,
                platforms,
            }),
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(formatApiError(data, "Не удалось сгенерировать контент."));
        }

        contentEl.value = data.content || "";
        imagePreferencesEl.value = data.image_preferences || imagePreferencesEl.value;
        imagePromptEl.value = data.image_prompt || "";
        imageUrlEl.value = data.image_url || "";
        updatePreview(data.image_url);
        updatePublishAvailability();
        updateImageAvailability();
        renderPublishPlaceholder();
        setLog(buildGenerateLog(data));
    } catch (error) {
        setLog(error.message);
    } finally {
        generateBtn.disabled = false;
        updatePublishAvailability();
        updateImageAvailability();
    }
}

async function regenerateImage() {
    if (!topicEl.value.trim()) {
        setLog("Сначала укажите тему поста.");
        topicEl.focus();
        return;
    }
    if (!contentEl.value.trim()) {
        setLog("Сначала нужен текст поста, чтобы подобрать подходящее изображение.");
        contentEl.focus();
        return;
    }

    regenerateImageBtn.disabled = true;
    setLog("Перегенерирую изображение по текущим параметрам...");

    try {
        const response = await fetch("/api/generate-image", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                topic: topicEl.value.trim(),
                tone: toneEl.value.trim() || "экспертный и дружелюбный",
                content: contentEl.value.trim(),
                image_preferences: imagePreferencesEl.value.trim() || null,
            }),
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(formatApiError(data, "Не удалось перегенерировать изображение."));
        }

        imagePromptEl.value = data.image_prompt || "";
        imageUrlEl.value = data.image_url || "";
        updatePreview(data.image_url);
        setLog("Изображение обновлено. Можно публиковать или перегенерировать еще раз с новым описанием.");
    } catch (error) {
        setLog(error.message);
    } finally {
        updateImageAvailability();
    }
}

async function publishPost() {
    const platforms = selectedPlatforms();
    if (!platforms.length) {
        setLog("Выберите хотя бы одну площадку для публикации.");
        return;
    }
    if (!contentEl.value.trim()) {
        setLog("Нет текста для публикации.");
        return;
    }

    publishBtn.disabled = true;
    renderPublishPending(platforms);
    setLog("Публикую пост в выбранные платформы...");

    try {
        const response = await fetch("/api/publish", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                content: contentEl.value,
                image_url: imageUrlEl.value.trim() || null,
                platforms,
            }),
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(formatApiError(data, "Не удалось опубликовать пост."));
        }
        if (!Array.isArray(data.results)) {
            throw new Error("Backend вернул некорректный ответ публикации.");
        }

        renderPublishResults(data.results, platforms);
        setLog(summarizePublishResults(data.results));
    } catch (error) {
        renderPublishRequestError(platforms, error.message);
        setLog(error.message);
    } finally {
        updatePublishAvailability();
    }
}

generateBtn.addEventListener("click", generatePost);
regenerateImageBtn.addEventListener("click", regenerateImage);
imageHintBtn.addEventListener("click", showImagePromptHint);
publishBtn.addEventListener("click", publishPost);
suggestTopicsBtn.addEventListener("click", suggestTopics);
refreshStatusBtn.addEventListener("click", () => {
    loadStatus().catch(() => {});
});
imageUrlEl.addEventListener("input", (event) => updatePreview(event.target.value.trim()));
contentEl.addEventListener("input", updatePublishAvailability);
contentEl.addEventListener("input", updateImageAvailability);
topicEl.addEventListener("input", updateImageAvailability);

loadStatus().catch(() => {
    setLog("Не удалось получить статус сервисов. Остальные поля остаются доступны.");
});
renderPublishPlaceholder();
renderTopicSuggestions();
updateImageAvailability();
