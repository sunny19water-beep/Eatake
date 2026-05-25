const { useEffect, useMemo, useRef, useState } = React;

const emptyTotals = { calories: 0, protein: 0, fat: 0, carbs: 0, fiber: 0, sugar: 0, salt: 0 };
const emptyManualMeal = {
  dish_name: "",
  calories: "",
  protein: "",
  fat: "",
  carbs: "",
  sugar: "",
  fiber: "",
  salt: "",
};
const defaultSettings = {
  age: "",
  weight: "",
  height: "",
  sex: "",
  basal_metabolism: "",
  exercise_per_week: "",
  target_weight: "",
  target_weeks: "",
  purpose: "健康維持",
  review_tone: "甘目",
};
const emptyProfile = { ready: false, bmi: 0, category: "未設定", target_daily_deficit: 0, kg_to_lose: 0, bmr: 0, tdee: 0, message: "" };
const emptyTargetPfc = { ready: false, calories: 0, protein: 0, fat: 0, carbs: 0, ratio: "" };
const emptyEnergy = { ready: false, bmr: 0, tdee: 0, calories: 0, balance: 0, deficit: 0, surplus: 0, target_daily_deficit: 0, target_pfc: emptyTargetPfc, profile: emptyProfile, message: "" };
const MIN_LOADING_MS = 1500;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function keepLoadingVisible(startedAt) {
  const remaining = MIN_LOADING_MS - (Date.now() - startedAt);
  if (remaining > 0) await wait(remaining);
}

function numericValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function suspiciousNutritionWarnings(meal) {
  const checks = [
    ["calories", "カロリー", "kcal", 3000],
    ["protein", "タンパク質", "g", 200],
    ["fat", "脂質", "g", 200],
    ["carbs", "炭水化物", "g", 400],
    ["sugar", "糖質", "g", 250],
    ["fiber", "食物繊維", "g", 80],
    ["salt", "塩分", "g", 10],
  ];
  return checks
    .filter(([key, , , limit]) => numericValue(meal[key]) > limit)
    .map(([key, label, unit, limit]) => `${label}: ${numericValue(meal[key])}${unit}（目安 ${limit}${unit}超）`);
}

function confirmSuspiciousNutrition(meal) {
  const warnings = suspiciousNutritionWarnings(meal);
  if (warnings.length === 0) return true;
  return window.confirm(
    `入力値がかなり大きいかもしれません。\n\n${warnings.join("\n")}\n\nこのまま保存しますか？`
  );
}

function Stat({ label, value, unit, tone }) {
  return React.createElement(
    "div",
    { className: `stat ${tone || ""}` },
    React.createElement("span", null, label),
    React.createElement("strong", null, value),
    React.createElement("small", null, unit)
  );
}

function MealItem({ meal, onDelete, onEdit }) {
  return React.createElement(
    "article",
    { className: meal.image_data ? "meal hasPhoto" : "meal noPhoto" },
    meal.image_data && React.createElement("img", { className: "mealPhoto", src: meal.image_data, alt: meal.dish_name }),
    React.createElement(
      "div",
      null,
      React.createElement("strong", null, meal.dish_name),
      React.createElement(
        "span",
        null,
        `${meal.calories} kcal / P${meal.protein} F${meal.fat} C${meal.carbs} / 糖質${meal.sugar}g 食物繊維${meal.fiber}g 塩分${meal.salt}g`
      ),
      meal.notes && React.createElement("em", null, meal.notes)
    ),
    React.createElement(
      "div",
      { className: "mealActions" },
      React.createElement("button", { onClick: () => onEdit(meal), className: "ghost smallButton" }, "編集"),
      React.createElement("button", { onClick: () => onDelete(meal.id), className: "delete smallButton" }, "取消")
    )
  );
}

function App() {
  const [view, setView] = useState("camera");
  const [recordView, setRecordView] = useState("daily");
  const [authReady, setAuthReady] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);
  const [authUser, setAuthUser] = useState(null);
  const [aiUsage, setAiUsage] = useState({ limit: 10, used: 0, remaining: 10 });
  const [selectedDate, setSelectedDate] = useState("");
  const [totals, setTotals] = useState(emptyTotals);
  const [energy, setEnergy] = useState(emptyEnergy);
  const [meals, setMeals] = useState([]);
  const [days, setDays] = useState([]);
  const [review, setReview] = useState(null);
  const [diary, setDiary] = useState({ text: "", created_at: "", updated_at: "" });
  const [diaryDraft, setDiaryDraft] = useState("");
  const [diaryMessage, setDiaryMessage] = useState("");
  const [streak, setStreak] = useState(null);
  const [weeklySummary, setWeeklySummary] = useState(null);
  const [weekData, setWeekData] = useState(null);
  const [settings, setSettings] = useState(defaultSettings);
  const [profile, setProfile] = useState(emptyProfile);
  const [purposes, setPurposes] = useState(["ダイエット", "増量", "健康維持", "減量"]);
  const [reviewTones, setReviewTones] = useState(["甘目", "普通", "厳しめ"]);
  const [cameraOn, setCameraOn] = useState(false);
  const [captureMode, setCaptureMode] = useState("meal");
  const [busy, setBusy] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [diaryBusy, setDiaryBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingTip, setLoadingTip] = useState("");
  const [message, setMessage] = useState("写真を撮るだけで記録します");
  const [settingsMessage, setSettingsMessage] = useState("");
  const [quickText, setQuickText] = useState("");
  const [manualMeal, setManualMeal] = useState(emptyManualMeal);
  const [tapCount, setTapCount] = useState(0);
  const [lastRecordTaps, setLastRecordTaps] = useState(null);
  const [preview, setPreview] = useState("");
  const [successText, setSuccessText] = useState("");
  const [successBurst, setSuccessBurst] = useState(false);
  const [quickDate, setQuickDate] = useState("");
  const [pendingEstimate, setPendingEstimate] = useState(null);
  const [editingMeal, setEditingMeal] = useState(null);
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [updateReady, setUpdateReady] = useState(false);
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const fileInputRef = useRef(null);

  const latestMeal = meals[0];
  const status = useMemo(() => {
    if (busy) return "解析中...";
    if (latestMeal && view === "camera") return `${latestMeal.dish_name} を追加しました`;
    return message;
  }, [busy, latestMeal, message, view]);

  useEffect(() => {
    refreshAll();
    watchForAppUpdates();
    return () => stopCamera();
  }, []);

  useEffect(() => {
    if (!successText) return;
    setSuccessBurst(true);
    const burstTimer = setTimeout(() => setSuccessBurst(false), 1400);
    const timer = setTimeout(() => setSuccessText(""), 3000);
    return () => {
      clearTimeout(burstTimer);
      clearTimeout(timer);
    };
  }, [successText]);

  useEffect(() => {
    const active = refreshing || busy || reviewBusy || settingsBusy || diaryBusy;
    if (!active) return;
    const tips = buildTips();
    setLoadingTip(tips[Math.floor(Math.random() * tips.length)] || "今日は1件だけ残せたら十分です。");
  }, [refreshing, busy, reviewBusy, settingsBusy, diaryBusy]);

  async function refreshAll() {
    const loadingStartedAt = Date.now();
    setRefreshing(true);
    try {
      const authRes = await fetch("/api/auth/me");
      const authData = await authRes.json();
      setAuthRequired(Boolean(authData.auth_required));
      setAuthUser(authData.user || null);
      setAiUsage(authData.ai_usage || aiUsage);
      setAuthReady(true);
      if (authData.auth_required && !authData.user) return;

      const [todayRes, daysRes, settingsRes, weekRes] = await Promise.all([
        fetch("/api/today"),
        fetch("/api/days"),
        fetch("/api/settings"),
        fetch("/api/week"),
      ]);
      const todayData = await todayRes.json();
      if (todayRes.status === 401) {
        setAuthRequired(true);
        setAuthUser(null);
        return;
      }
      const daysData = await daysRes.json();
      const settingsData = await settingsRes.json();
      const weekJson = await weekRes.json();
      setSelectedDate(todayData.date);
      setQuickDate(todayData.date);
      setTotals(todayData.totals || emptyTotals);
      setEnergy(todayData.energy || emptyEnergy);
      setMeals(todayData.meals || []);
      setReview(todayData.review || null);
      setDiary(todayData.diary || { text: "", created_at: "", updated_at: "" });
      setDiaryDraft(todayData.diary?.text || "");
      setDiaryMessage("");
      setStreak(todayData.streak || null);
      setWeeklySummary(todayData.weekly_summary || null);
      setAiUsage(todayData.ai_usage || aiUsage);
      setWeekData(weekJson);
      setDays(daysData.days || []);
      setSettings(settingsData.settings || defaultSettings);
      setProfile(settingsData.profile || todayData.energy?.profile || emptyProfile);
      setPurposes(settingsData.purposes || purposes);
      setReviewTones(settingsData.review_tones || reviewTones);
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setRefreshing(false);
    }
  }

  function watchForAppUpdates() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.getRegistration().then((registration) => {
      if (!registration) return;
      registration.addEventListener("updatefound", () => {
        const worker = registration.installing;
        if (!worker) return;
        worker.addEventListener("statechange", () => {
          if (worker.state === "installed" && navigator.serviceWorker.controller) {
            setUpdateReady(true);
          }
        });
      });
      registration.update();
    });
  }

  function reloadApp() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.getRegistration().then((registration) => registration?.waiting?.postMessage({ type: "SKIP_WAITING" }));
    }
    window.location.reload();
  }

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    setAuthUser(null);
    setAuthRequired(true);
  }

  async function loadDay(day) {
    if (!day) return;
    const loadingStartedAt = Date.now();
    setRefreshing(true);
    try {
      const response = await fetch(`/api/days/${day}`);
      const data = await response.json();
      setSelectedDate(data.date);
      setTotals(data.totals || emptyTotals);
      setEnergy(data.energy || emptyEnergy);
      setMeals(data.meals || []);
      setReview(data.review || null);
      setDiary(data.diary || { text: "", created_at: "", updated_at: "" });
      setDiaryDraft(data.diary?.text || "");
      setDiaryMessage("");
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setRefreshing(false);
    }
  }

  async function reloadDays() {
    const response = await fetch("/api/days");
    const data = await response.json();
    setDays(data.days || []);
  }

  async function startCamera() {
    setTapCount((count) => count + 1);
    setMessage("カメラを準備しています");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
        audio: false,
      });
      streamRef.current = stream;
      videoRef.current.srcObject = stream;
      setCameraOn(true);
      setMessage(captureMode === "label" ? "栄養成分表示を写してシャッター" : "料理を写してシャッター");
    } catch (error) {
      setMessage("カメラが使えないため写真選択に切り替えます");
      fileInputRef.current.click();
    }
  }

  function stopCamera() {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    setCameraOn(false);
  }

  async function captureAndSend() {
    setTapCount((count) => count + 1);
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
    setPreview(dataUrl);
    canvas.toBlob((blob) => {
      if (blob) sendImage(blob, "camera.jpg");
    }, "image/jpeg", 0.85);
  }

  async function sendImage(blob, filename) {
    const loadingStartedAt = Date.now();
    setBusy(true);
    setMessage(captureMode === "label" ? "栄養成分表示を読み取っています" : "Geminiで推定しています");
    const formData = new FormData();
    formData.append("file", blob, filename);

    try {
      const endpoint = captureMode === "label" ? "/api/estimate-label" : "/api/estimate";
      const response = await fetch(endpoint, { method: "POST", body: formData });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "解析に失敗しました");
      setPendingEstimate({
        ...data.estimate,
        image_data: data.image_data || "",
        date: quickDate || selectedDate,
        mode: captureMode,
      });
      setAiUsage(data.ai_usage || aiUsage);
      setMessage("推定結果を確認してください");
    } catch (error) {
      setMessage(error.message);
      setCaptureMode("text");
      setQuickText((current) => current || "");
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setBusy(false);
    }
  }

  async function confirmPendingEstimate() {
    if (!pendingEstimate) return;
    const loadingStartedAt = Date.now();
    setBusy(true);
    try {
      const response = await fetch("/api/meals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pendingEstimate),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "保存に失敗しました");
      setSelectedDate(data.meal.date);
      setQuickDate(data.meal.date);
      setTotals(data.totals);
      setEnergy(data.energy || emptyEnergy);
      setMeals((current) => [data.meal, ...current.filter((meal) => meal.id !== data.meal.id)]);
      setDays(data.days || days);
      setReview(null);
      setAiUsage(data.ai_usage || aiUsage);
      setLastRecordTaps(tapCount + 1);
      setTapCount(0);
      setMessage("今日の合計に自動加算しました");
      setSuccessText(pendingEstimate.mode === "label" ? "成分表示を記録しました" : "食事を記録しました");
      setPendingEstimate(null);
    } catch (error) {
      setMessage(error.message);
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setBusy(false);
    }
  }

  async function handleFile(event) {
    setTapCount((count) => count + 1);
    const file = event.target.files?.[0];
    if (!file) return;
    setPreview(URL.createObjectURL(file));
    await sendImage(file, file.name);
    event.target.value = "";
  }

  async function deleteMeal(id) {
    const response = await fetch(`/api/meals/${id}`, { method: "DELETE" });
    const data = await response.json();
    if (response.ok) {
      setTotals(data.totals);
      setEnergy(data.energy || emptyEnergy);
      setMeals(data.meals);
      setDays(data.days || days);
      setReview(null);
      setMessage("取り消しました");
    }
  }

  async function createReview() {
    setTapCount((count) => count + 1);
    const loadingStartedAt = Date.now();
    setReviewBusy(true);
    try {
      const response = await fetch(`/api/days/${selectedDate}/review`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "レビュー作成に失敗しました");
      setReview(data.review);
      setAiUsage(data.ai_usage || aiUsage);
    } catch (error) {
      setReview({ text: error.message, created_at: "" });
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setReviewBusy(false);
    }
  }

  async function saveDiary() {
    const loadingStartedAt = Date.now();
    setDiaryBusy(true);
    setDiaryMessage("");
    try {
      const response = await fetch(`/api/days/${selectedDate}/diary`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: diaryDraft }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "日記の保存に失敗しました");
      setDiary(data.diary || { text: diaryDraft, created_at: "", updated_at: "" });
      setDiaryMessage("日記を保存しました");
    } catch (error) {
      setDiaryMessage(error.message);
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setDiaryBusy(false);
    }
  }

  async function saveSettings() {
    const loadingStartedAt = Date.now();
    setSettingsBusy(true);
    setSettingsMessage("");
    try {
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "設定の保存に失敗しました");
      setSettings(data.settings);
      setProfile(data.profile || emptyProfile);
      setPurposes(data.purposes || purposes);
      setReviewTones(data.review_tones || reviewTones);
      setSettingsMessage("保存しました");
    } catch (error) {
      setSettingsMessage(error.message);
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setSettingsBusy(false);
    }
  }

  async function saveQuickText() {
    const text = quickText.trim();
    if (!text) return;
    const loadingStartedAt = Date.now();
    setBusy(true);
    setTapCount((count) => count + 1);
    try {
      const response = await fetch("/api/analyze-text", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, date: quickDate || selectedDate }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "てきとう記録に失敗しました");
      setQuickText("");
      setSelectedDate(data.meal.date);
      setTotals(data.totals);
      setEnergy(data.energy || emptyEnergy);
      setMeals((current) => [data.meal, ...current.filter((meal) => meal.id !== data.meal.id)]);
      setDays(data.days || days);
      setReview(null);
      setAiUsage(data.ai_usage || aiUsage);
      setLastRecordTaps(1);
      setTapCount(0);
      setMessage("てきとう記録、ちゃんと残せました");
      setSuccessText("文面記録を追加しました");
    } catch (error) {
      setMessage(error.message);
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setBusy(false);
    }
  }

  async function saveManualMeal() {
    const hasRequiredValue = manualMeal.dish_name.trim() && String(manualMeal.calories).trim();
    if (!hasRequiredValue) return;
    if (!confirmSuspiciousNutrition(manualMeal)) return;
    const loadingStartedAt = Date.now();
    setBusy(true);
    setTapCount((count) => count + 1);
    try {
      const payload = {
        ...manualMeal,
        date: quickDate || selectedDate,
        confidence: 1,
        notes: "成分表示を手入力",
      };
      const response = await fetch("/api/meals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "手入力の保存に失敗しました");
      setManualMeal(emptyManualMeal);
      setSelectedDate(data.meal.date);
      setQuickDate(data.meal.date);
      setTotals(data.totals);
      setEnergy(data.energy || emptyEnergy);
      setMeals((current) => [data.meal, ...current.filter((meal) => meal.id !== data.meal.id)]);
      setDays(data.days || days);
      setReview(null);
      setAiUsage(data.ai_usage || aiUsage);
      setLastRecordTaps(1);
      setTapCount(0);
      setMessage("成分表示の数値をそのまま記録しました");
      setSuccessText("成分表示を手入力で記録しました");
    } catch (error) {
      setMessage(error.message);
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setBusy(false);
    }
  }

  function setManualField(key, value) {
    setManualMeal((current) => ({ ...current, [key]: value }));
  }

  function setSetting(key, value) {
    setSettings((current) => ({ ...current, [key]: value }));
  }

  function renderEncouragement() {
    return React.createElement(
      "section",
      { className: "encouragement" },
      React.createElement("strong", null, streak ? streak.message : "今日も1枚だけでOK"),
      weeklySummary?.show && React.createElement("span", null, weeklySummary.text),
      React.createElement("small", null, `AI解析 あと${aiUsage.remaining}回 / 1日${aiUsage.limit}回`),
      lastRecordTaps && React.createElement("small", null, `記録完了まで ${lastRecordTaps} タップ`)
    );
  }

  async function saveMealEdit() {
    if (!editingMeal) return;
    if (!confirmSuspiciousNutrition(editingMeal)) return;
    const loadingStartedAt = Date.now();
    setBusy(true);
    try {
      const response = await fetch(`/api/meals/${editingMeal.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(editingMeal),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "編集に失敗しました");
      setTotals(data.totals);
      setEnergy(data.energy || emptyEnergy);
      setMeals(data.meals);
      setDays(data.days || days);
      setReview(null);
      setEditingMeal(null);
      setSuccessText("記録を修正しました");
    } catch (error) {
      setMessage(error.message);
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setBusy(false);
    }
  }

  async function deleteAllData() {
    if (!window.confirm("すべての食事記録とレビューを削除します。元に戻せません。")) return;
    const loadingStartedAt = Date.now();
    setBusy(true);
    try {
      const response = await fetch("/api/meals", { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "削除に失敗しました");
      setTotals(data.totals || emptyTotals);
      setEnergy(data.energy || emptyEnergy);
      setMeals([]);
      setDays([]);
      setReview(null);
      setAiUsage(data.ai_usage || aiUsage);
      setSuccessText("記録を削除しました");
    } catch (error) {
      setSettingsMessage(error.message);
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setBusy(false);
    }
  }

  async function sendFeedback() {
    const message = feedbackText.trim();
    if (!message) return;
    const loadingStartedAt = Date.now();
    setSettingsBusy(true);
    setFeedbackMessage("");
    try {
      const response = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "送信に失敗しました");
      setFeedbackText("");
      setFeedbackMessage(data.message || "送信しました");
    } catch (error) {
      setFeedbackMessage(error.message);
    } finally {
      await keepLoadingVisible(loadingStartedAt);
      setSettingsBusy(false);
    }
  }

  function renderQuickRecord() {
    return React.createElement(
      "section",
      { className: "quickRecord" },
      React.createElement("label", null, "てきとう記録"),
      React.createElement("input", {
        type: "date",
        value: quickDate || selectedDate,
        onChange: (event) => setQuickDate(event.target.value),
      }),
      React.createElement("textarea", {
        value: quickText,
        rows: 2,
        placeholder: "例: コンビニのおにぎり1個 / ラーメン食べた",
        onChange: (event) => setQuickText(event.target.value),
      }),
      React.createElement(
        "button",
        { className: "primary wideButton", onClick: saveQuickText, disabled: busy || !quickText.trim() },
        busy ? "記録中" : "これで記録"
      )
    );
  }

  function renderManualNutrition() {
    const fields = [
      ["calories", "カロリー", "kcal", "number"],
      ["protein", "タンパク質", "g", "number"],
      ["fat", "脂質", "g", "number"],
      ["carbs", "炭水化物", "g", "number"],
      ["sugar", "糖質", "g", "number"],
      ["fiber", "食物繊維", "g", "number"],
      ["salt", "塩分", "g", "number"],
    ];
    return React.createElement(
      "section",
      { className: "quickRecord manualNutrition" },
      React.createElement("label", null, "成分表示を手入力"),
      React.createElement("input", {
        type: "date",
        value: quickDate || selectedDate,
        onChange: (event) => setQuickDate(event.target.value),
      }),
      React.createElement("input", {
        value: manualMeal.dish_name,
        placeholder: "商品名・食事名",
        onChange: (event) => setManualField("dish_name", event.target.value),
      }),
      React.createElement(
        "div",
        { className: "manualGrid" },
        fields.map(([key, label, unit, type]) =>
          React.createElement(
            "label",
            { key },
            React.createElement("span", null, `${label} (${unit})`),
            React.createElement("input", {
              type,
              min: "0",
              step: key === "calories" ? "1" : "0.1",
              inputMode: "decimal",
              value: manualMeal[key],
              placeholder: "0",
              onChange: (event) => setManualField(key, event.target.value),
            })
          )
        )
      ),
      React.createElement("small", null, "糖質が空の場合は、炭水化物から食物繊維を引いた値で自動補完されます。"),
      React.createElement(
        "button",
        {
          className: "primary wideButton",
          onClick: saveManualMeal,
          disabled: busy || !manualMeal.dish_name.trim() || !String(manualMeal.calories).trim(),
        },
        busy ? "記録中" : "この成分で記録"
      )
    );
  }

  function renderTotals() {
    return React.createElement(
      "section",
      { className: "totals" },
      React.createElement(Stat, { label: "カロリー", value: totals.calories, unit: "kcal", tone: "wide" }),
      React.createElement(Stat, { label: "タンパク質", value: totals.protein, unit: "g" }),
      React.createElement(Stat, { label: "脂質", value: totals.fat, unit: "g" }),
      React.createElement(Stat, { label: "炭水化物", value: totals.carbs, unit: "g" }),
      React.createElement(Stat, { label: "糖質", value: totals.sugar, unit: "g" }),
      React.createElement(Stat, { label: "食物繊維", value: totals.fiber, unit: "g" }),
      React.createElement(Stat, { label: "塩分", value: totals.salt, unit: "g" })
    );
  }

  function renderPrototypeBanner() {
    if (authRequired) return null;
    return React.createElement(
      "section",
      { className: "prototypeBanner" },
      React.createElement("strong", null, "プロトタイプ版"),
      React.createElement("span", null, "ログインなし公開中です。入力した内容は共通のテスト領域に保存されます。")
    );
  }

  function renderPendingEstimate() {
    if (!pendingEstimate) return null;
    return React.createElement(
      "section",
      { className: "pendingEstimate" },
      pendingEstimate.image_data && React.createElement("img", { src: pendingEstimate.image_data, alt: "" }),
      React.createElement(
        "div",
        null,
        React.createElement("h2", null, "この内容で記録しますか？"),
        React.createElement("strong", null, pendingEstimate.dish_name),
        React.createElement(
          "p",
          null,
          `${pendingEstimate.calories}kcal / P${pendingEstimate.protein}g F${pendingEstimate.fat}g C${pendingEstimate.carbs}g`
        ),
        React.createElement("small", null, `記録日: ${pendingEstimate.date || selectedDate}`)
      ),
      React.createElement(
        "div",
        { className: "confirmActions" },
        React.createElement("button", { className: "ghost", onClick: () => setPendingEstimate(null), disabled: busy }, "やり直す"),
        React.createElement("button", { className: "primary", onClick: confirmPendingEstimate, disabled: busy }, busy ? "保存中" : "これで記録")
      )
    );
  }

  function renderEnergyBalance() {
    const balanceText = energy.balance > 0 ? `+${energy.balance}` : `${energy.balance}`;
    return React.createElement(
      "section",
      { className: "energyBox" },
      React.createElement(
        "div",
        null,
        React.createElement("span", null, "推定TDEE"),
        React.createElement("strong", null, energy.ready ? `${energy.tdee} kcal` : "未設定"),
        React.createElement("small", null, energy.ready ? `BMR ${energy.bmr} kcal` : energy.message || "設定を保存すると表示します")
      ),
      React.createElement(
        "div",
        null,
        React.createElement("span", null, "摂取との差分"),
        React.createElement("strong", { className: energy.balance <= 0 ? "deficit" : "surplus" }, energy.ready ? `${balanceText} kcal` : "-"),
      React.createElement("small", null, energy.ready ? (energy.deficit > 0 ? `赤字 ${energy.deficit} kcal` : `超過 ${energy.surplus} kcal`) : "年齢・体重・身長・性別が必要")
      ),
      energy.ready &&
        React.createElement(
          "div",
          null,
          React.createElement("span", null, "目標赤字"),
          React.createElement("strong", null, energy.target_daily_deficit ? `${energy.target_daily_deficit} kcal` : "-"),
          React.createElement("small", null, energy.target_daily_deficit ? `目標まで ${energy.profile.kg_to_lose} kg` : "目標体重と週数を設定")
        )
    );
  }

  function renderTargetPfc() {
    const target = energy.target_pfc || emptyTargetPfc;
    return React.createElement(
      "section",
      { className: "targetPfc" },
      React.createElement("h2", null, "目標PFC"),
      React.createElement(
        "div",
        null,
        React.createElement("span", null, target.ready ? `${target.calories} kcal / ${target.ratio}` : "設定を保存すると表示します"),
        target.ready &&
          React.createElement(
            "strong",
            null,
            `P${target.protein}g F${target.fat}g C${target.carbs}g`
          )
      )
    );
  }

  function renderPfcGapGraph() {
    const target = energy.target_pfc || emptyTargetPfc;
    const items = [
      ["protein", "タンパク質", "P", "g"],
      ["fat", "脂質", "F", "g"],
      ["carbs", "炭水化物", "C", "g"],
    ];
    if (!target.ready) {
      return React.createElement(
        "section",
        { className: "pfcGapBox" },
        React.createElement("h2", null, "PFC推奨量との差"),
        React.createElement("p", null, "設定を保存すると、推奨PFCと不足量を表示します。")
      );
    }
    return React.createElement(
      "section",
      { className: "pfcGapBox" },
      React.createElement("h2", null, "PFC推奨量との差"),
      React.createElement("p", null, `${target.calories} kcal / ${target.ratio}`),
      React.createElement(
        "div",
        { className: "pfcBars" },
        items.map(([key, label, short, unit]) => {
          const recommended = Number(target[key]) || 0;
          const current = Number(totals[key]) || 0;
          const gap = Math.max(0, Math.round((recommended - current) * 10) / 10);
          const over = Math.max(0, Math.round((current - recommended) * 10) / 10);
          const percent = recommended > 0 ? Math.min(100, Math.round((current / recommended) * 100)) : 0;
          return React.createElement(
            "article",
            { key },
            React.createElement(
              "div",
              { className: "pfcBarHead" },
              React.createElement("strong", null, `${short} ${label}`),
              React.createElement("span", null, `${current}/${recommended}${unit}`)
            ),
            React.createElement(
              "div",
              { className: "pfcTrack" },
              React.createElement("span", { style: { width: `${percent}%` } })
            ),
            React.createElement(
              "small",
              { className: gap > 0 ? "shortage" : "complete" },
              gap > 0 ? `あと ${gap}${unit}` : `達成${over > 0 ? ` +${over}${unit}` : ""}`
            )
          );
        })
      )
    );
  }

  function renderAiNotice() {
    return React.createElement(
      "section",
      { className: "aiNotice" },
      "AI識別による推定値です。食事量や写り方で変わるため、大体の目安としてお考えください。"
    );
  }

  function renderProfileSummary() {
    return React.createElement(
      "section",
      { className: "profileSummary" },
      React.createElement(
        "div",
        null,
        React.createElement("span", null, "BMI"),
        React.createElement("strong", null, profile.ready ? profile.bmi : "-"),
        React.createElement("small", null, profile.category || "未設定")
      ),
      React.createElement(
        "div",
        null,
        React.createElement("span", null, "推定TDEE"),
        React.createElement("strong", null, profile.tdee ? `${profile.tdee} kcal` : "-"),
        React.createElement("small", null, profile.bmr ? `基礎代謝 ${profile.bmr} kcal` : "基礎代謝かプロフィールを設定")
      ),
      React.createElement(
        "div",
        null,
        React.createElement("span", null, "目標赤字 / 日"),
        React.createElement("strong", null, profile.target_daily_deficit ? `${profile.target_daily_deficit} kcal` : "-"),
        React.createElement("small", null, profile.kg_to_lose ? `${profile.kg_to_lose} kg減を目標` : "目標体重と週数を設定")
      )
    );
  }

  function renderSetupGuide() {
    const missing = [];
    if (!settings.age) missing.push("年齢");
    if (!settings.weight) missing.push("体重");
    if (!settings.height) missing.push("身長");
    if (!settings.sex) missing.push("性別");
    if (!settings.purpose) missing.push("目的");
    if (missing.length === 0) return null;

    return React.createElement(
      "section",
      { className: "setupGuide" },
      React.createElement(
        "div",
        null,
        React.createElement("strong", null, "最初に設定すると、目標PFCとTDEEが出せます"),
        React.createElement("span", null, missing.length ? `未入力: ${missing.join("・")}` : "目標PFCの計算に設定が必要です")
      ),
      React.createElement("button", { className: "ghost", onClick: () => setView("settings") }, "設定する")
    );
  }

  function renderReviewText() {
    if (!review) {
      return React.createElement(
        "p",
        null,
        "今日の記録を確定すると、AIが食事の振り返り、記録できたこと、継続、次の一手をまとめます"
      );
    }
    const titles = ["今日の振り返り", "よかった点", "継続のこと", "次の一手"];
    const text = review.text || "";
    const sections = titles.map((title, index) => {
      const start = text.indexOf(title);
      if (start === -1) return null;
      const nextStarts = titles
        .slice(index + 1)
        .map((nextTitle) => text.indexOf(nextTitle))
        .filter((position) => position > start);
      const end = nextStarts.length ? Math.min(...nextStarts) : text.length;
      const body = text.slice(start + title.length, end).replace(/^[:：\s\n]+/, "").trim();
      return { title, body };
    }).filter(Boolean);

    if (sections.length < 2) {
      return React.createElement("p", null, text);
    }

    return React.createElement(
      "div",
      { className: "reviewSections" },
      sections.map((section) =>
        React.createElement(
          "article",
          { key: section.title },
          React.createElement("h3", null, section.title),
          React.createElement("p", null, section.body)
        )
      )
    );
  }

  function renderReviewBox() {
    return React.createElement(
      "section",
      { className: "reviewBox" },
      React.createElement(
        "div",
        null,
        React.createElement("h2", null, "AIレビュー"),
        renderReviewText()
      ),
      React.createElement(
        "button",
        { className: "primary", onClick: createReview, disabled: reviewBusy },
        reviewBusy ? "作成中" : "今日の記録を確定"
      )
    );
  }

  function renderMealList(emptyText) {
    return React.createElement(
      "section",
      { className: "history" },
      meals.length === 0 && React.createElement("p", { className: "empty" }, emptyText),
      meals.map((meal, index) =>
        React.createElement(
          React.Fragment,
          { key: meal.id },
          index > 0 && React.createElement("div", { className: "mealDivider" }),
          React.createElement(MealItem, { meal, onDelete: deleteMeal, onEdit: setEditingMeal })
        )
      )
    );
  }

  function renderEditMealModal() {
    if (!editingMeal) return null;
    const fields = [
      ["dish_name", "食事名", "text"],
      ["calories", "カロリー kcal", "number"],
      ["protein", "タンパク質 g", "number"],
      ["fat", "脂質 g", "number"],
      ["carbs", "炭水化物 g", "number"],
      ["sugar", "糖質 g", "number"],
      ["fiber", "食物繊維 g", "number"],
      ["salt", "塩分 g", "number"],
    ];
    return React.createElement(
      "div",
      { className: "modalBackdrop" },
      React.createElement(
        "section",
        { className: "editModal" },
        React.createElement("h2", null, "記録を編集"),
        React.createElement(
          "div",
          { className: "editGrid" },
          fields.map(([key, label, type]) =>
            React.createElement(
              "label",
              { key },
              label,
              React.createElement("input", {
                type,
                inputMode: type === "number" ? "decimal" : undefined,
                value: editingMeal[key] ?? "",
                onChange: (event) => setEditingMeal((current) => ({ ...current, [key]: event.target.value })),
              })
            )
          ),
          React.createElement(
            "label",
            { className: "wideEdit" },
            "メモ",
            React.createElement("textarea", {
              value: editingMeal.notes || "",
              rows: 3,
              onChange: (event) => setEditingMeal((current) => ({ ...current, notes: event.target.value })),
            })
          )
        ),
        React.createElement(
          "div",
          { className: "modalActions" },
          React.createElement("button", { className: "ghost", onClick: () => setEditingMeal(null) }, "閉じる"),
          React.createElement("button", { className: "primary", onClick: saveMealEdit, disabled: busy }, busy ? "保存中" : "保存")
        )
      )
    );
  }

  function renderPrivacyPanel() {
    return React.createElement(
      "details",
      { className: "privacyPanel" },
      React.createElement("summary", null, "プライバシーとデータ削除"),
      React.createElement("p", null, "Eatakeは、食事写真、栄養推定結果、体重などの設定情報、AIレビューを記録します。写真は表示用に小さく圧縮して保存します。"),
      React.createElement("p", null, "プロトタイプ中はログインなしで使えるため、公開環境では同じプロトタイプ領域に保存されます。個人情報を含む写真やメモは入れすぎないでください。"),
      React.createElement("p", null, "本公開時は認証を有効にして、ユーザーごとに保存領域を分ける想定です。"),
      React.createElement("button", { className: "delete wideButton", onClick: deleteAllData, disabled: busy }, "すべての記録を削除")
    );
  }

  function renderFeedbackPanel() {
    return React.createElement(
      "section",
      { className: "feedbackPanel" },
      React.createElement("h2", null, "不具合・要望"),
      React.createElement("textarea", {
        value: feedbackText,
        rows: 4,
        placeholder: "例: 解析が外れた、画面が見づらい、こういう機能がほしい",
        onChange: (event) => setFeedbackText(event.target.value),
      }),
      React.createElement("button", { className: "primary wideButton", onClick: sendFeedback, disabled: settingsBusy || !feedbackText.trim() }, settingsBusy ? "送信中" : "送信"),
      feedbackMessage && React.createElement("p", { className: "formMessage" }, feedbackMessage)
    );
  }

  function buildTips() {
    const target = energy.target_pfc || emptyTargetPfc;
    const tips = [];
    const dayDate = selectedDate ? new Date(`${selectedDate}T00:00:00`) : new Date();
    const weekNames = ["日", "月", "火", "水", "木", "金", "土"];
    const dayLabel = selectedDate ? selectedDate.slice(5).replace("-", "/") : "今日";
    const weekday = weekNames[dayDate.getDay()];
    const dayFacts = [
      `${dayLabel}は${weekday}曜日です。週の流れを整えるなら、今日は「1食だけ記録」で十分です。`,
      `${dayLabel}の記録を開いています。食事だけでなく、体調や空腹感も日記に一言残すと後から見返しやすいです。`,
      `${dayLabel}は何の日でも、Eatakeでは「続けた日」です。完璧より、残したことを優先してOK。`,
    ];
    tips.push(dayFacts[Math.floor(Math.random() * dayFacts.length)]);
    tips.push("精度は「栄養成分表示の撮影 > 文面記録 > 食事写真」の順で安定しやすいです。迷ったら成分表示が一番強いです。");
    tips.push("食事写真は明るい場所で、皿全体が入るように真上か斜め45度から撮ると推定が安定しやすいです。");
    tips.push("料理名が分かる時は文面記録もかなり便利です。「コンビニおにぎり1個、サラダチキン」みたいに書くと精度が上がります。");
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayKey = yesterday.toISOString().slice(0, 10);
    const yesterdayLog = days.find((day) => day.date === yesterdayKey);
    const todayMealCount = meals.length;
    const weekAverage = weekData?.averages;

    if (yesterdayLog?.meal_count > 0) {
      tips.push(`昨日は${yesterdayLog.meal_count}食、約${yesterdayLog.calories}kcalを記録できていました。昨日できた流れを、今日は1枚だけでもつなげれば十分です。`);
    } else if (days.length > 0) {
      tips.push("昨日は休憩日でも大丈夫。今日また1食だけ残せば、ちゃんと再スタートになります。");
    }

    if (todayMealCount > 0) {
      tips.push(`今日はすでに${todayMealCount}件記録できています。精度より、残せていることが一番の勝ちです。`);
    } else {
      tips.push("今日はまだ記録なし。写真が無理なら、文面記録で「おにぎり1個」だけでもOKです。");
    }

    if (aiUsage.remaining <= 2) {
      tips.push(`AI解析は今日はあと${aiUsage.remaining}回です。迷ったら写真より文面記録を使うと、回数を温存できます。`);
    } else {
      tips.push(`AI解析は今日はあと${aiUsage.remaining}回使えます。よく分からない食事ほど写真で残すと後から見返しやすいです。`);
    }

    if (weekAverage?.calories > 0) {
      tips.push(`今週の平均は約${weekAverage.calories}kcal/日です。1日だけで判断せず、週平均でゆるく見るのが続けやすいです。`);
    }

    if (!energy.ready) {
      tips.push("設定を入れると、TDEEと目標PFCがあなた用になります。まずは年齢・身長・体重だけでもOK。");
    } else if (energy.deficit > 0) {
      tips.push(`今日はTDEEより約${energy.deficit}kcal下。減量目的なら、この差を急に大きくしすぎないのが続けやすいです。`);
    } else if (energy.surplus > 0) {
      tips.push(`今日はTDEEより約${energy.surplus}kcal上。増量なら良い材料、減量なら次の食事を軽めにするだけで十分です。`);
    }
    if (target.ready) {
      const proteinGap = Math.round((target.protein - totals.protein) * 10) / 10;
      if (proteinGap > 0) tips.push(`タンパク質はあと${proteinGap}gくらいが目安。卵、豆腐、鶏肉、ヨーグルトあたりが足しやすいです。`);
      if (totals.fiber < 10) tips.push("食物繊維は少し足せると満足感が出やすいです。野菜、海藻、きのこを少し足すだけでもOK。");
    }
    if (streak?.streak >= 3) {
      tips.push(`${streak.streak}日連続はかなり良い流れです。今日は完璧より、記録を切らさないことが勝ちです。`);
    } else {
      tips.push("記録は毎食完璧じゃなくて大丈夫。写真1枚か文面1行だけでも、次の改善につながります。");
    }

    return tips;
  }

  function renderTips() {
    return React.createElement(
      "section",
      { className: "tipsBox" },
      React.createElement("h2", null, "Tips"),
      buildTips().slice(0, 1).map((tip, index) =>
        React.createElement(
          "article",
          { key: tip },
          React.createElement("strong", null, `Tip ${index + 1}`),
          React.createElement("p", null, tip)
        )
      )
    );
  }

  function renderLoadingOverlay() {
    const active = refreshing || busy || reviewBusy || settingsBusy || diaryBusy;
    if (!active) return null;
    const text = busy
      ? "記録しています"
      : reviewBusy
        ? "レビューを作っています"
        : diaryBusy
          ? "日記を保存しています"
          : settingsBusy
            ? "設定を保存しています"
            : "更新しています";

    return React.createElement(
      "div",
      { className: "loadingOverlay", role: "status", "aria-live": "polite" },
      React.createElement(
        "div",
        { className: "loadingPanel" },
        React.createElement("span", { className: "spinner" }),
        React.createElement("strong", null, text),
        React.createElement("small", null, "少しだけ待ってください"),
        React.createElement("p", { className: "loadingTip" }, loadingTip || "精度は栄養成分表示、文面記録、食事写真の順で安定しやすいです。")
      )
    );
  }

  function renderCamera() {
    return React.createElement(
      React.Fragment,
      null,
      renderPrototypeBanner(),
      renderEncouragement(),
      renderSetupGuide(),
      React.createElement(
        "section",
        { className: "fastStart" },
        React.createElement("button", { className: "bigCapture", onClick: startCamera, disabled: busy }, "すぐ撮る"),
        React.createElement(
          "button",
          {
            className: captureMode === "text" ? "textShortcut active" : "textShortcut",
            onClick: () => {
              setCaptureMode("text");
              setMessage("文面だけでも記録できます");
              stopCamera();
            },
            disabled: busy,
          },
          "文面で残す"
        )
      ),
      React.createElement(
        "section",
        { className: "modeSwitch" },
        React.createElement(
          "button",
          {
            className: captureMode === "meal" ? "active" : "",
            onClick: () => {
              setCaptureMode("meal");
              setMessage("食事写真から栄養を推定します");
            },
            disabled: busy,
          },
          "食事写真"
        ),
        React.createElement(
          "button",
          {
            className: captureMode === "label" ? "active" : "",
            onClick: () => {
              setCaptureMode("label");
              setMessage("栄養成分表示の数値を採用します");
            },
            disabled: busy,
          },
          "成分表示解析"
        ),
        React.createElement(
          "button",
          {
            className: captureMode === "manual" ? "active" : "",
            onClick: () => {
              setCaptureMode("manual");
              setMessage("成分表示の数値を写真なしで記録できます");
              stopCamera();
            },
            disabled: busy,
          },
          "成分手入力"
        )
      ),
      renderPendingEstimate(),
      captureMode !== "text" &&
        captureMode !== "manual" &&
        React.createElement(
          "section",
          { className: "camera" },
          React.createElement("video", {
            ref: videoRef,
            autoPlay: true,
            playsInline: true,
            muted: true,
            className: cameraOn ? "video isOn" : "video",
          }),
          !cameraOn && preview && React.createElement("img", { className: "preview", src: preview, alt: "" }),
          !cameraOn && !preview && React.createElement("div", { className: "placeholder" }, captureMode === "label" ? "NUTRITION LABEL" : "CAMERA"),
          React.createElement("p", { className: "status" }, status),
          React.createElement(
            "div",
            { className: "actions" },
            React.createElement(
              "button",
              {
                className: "shutter primaryCapture",
                disabled: busy,
                onClick: cameraOn ? captureAndSend : startCamera,
              },
              busy ? "解析中" : cameraOn ? "撮る" : "カメラ"
            ),
            React.createElement(
              "button",
              { className: "secondary", onClick: () => fileInputRef.current.click(), disabled: busy },
              "写真"
            )
          ),
          React.createElement("input", {
            ref: fileInputRef,
            className: "file",
            type: "file",
            accept: "image/*",
            capture: "environment",
            onChange: handleFile,
          })
        ),
      captureMode === "text" && renderQuickRecord(),
      captureMode === "manual" && renderManualNutrition(),
      renderTotals(),
      renderAiNotice()
    );
  }

  function renderDiary() {
    return React.createElement(
      "section",
      { className: "diaryBox" },
      React.createElement(
        "div",
        { className: "diaryHead" },
        React.createElement("h2", null, "日記"),
        diary.updated_at && React.createElement("span", null, `保存済み ${diary.updated_at.slice(5, 16).replace("T", " ")}`)
      ),
      React.createElement("textarea", {
        value: diaryDraft,
        rows: 4,
        maxLength: 2000,
        placeholder: "例: 今日は昼にラーメン。夜は軽めにしたい。体調は普通。",
        onChange: (event) => setDiaryDraft(event.target.value),
      }),
      React.createElement(
        "div",
        { className: "diaryActions" },
        React.createElement("small", null, `${diaryDraft.length}/2000`),
        React.createElement("button", { className: "primary", onClick: saveDiary, disabled: diaryBusy }, diaryBusy ? "保存中" : "日記を保存")
      ),
      diaryMessage && React.createElement("p", { className: "formMessage" }, diaryMessage)
    );
  }

  function renderDaily() {
    return React.createElement(
      React.Fragment,
      null,
      React.createElement(
        "section",
        { className: "sectionHead dailyHead" },
        React.createElement("h2", null, "1日の記録"),
        React.createElement("p", null, selectedDate),
        renderDiary()
      ),
      renderTotals(),
      renderPfcGapGraph(),
      renderAiNotice(),
      renderEncouragement(),
      renderMealList("この日の記録はまだありません")
    );
  }

  function renderRecord() {
    return React.createElement(
      React.Fragment,
      null,
      React.createElement(
        "section",
        { className: "recordSwitch" },
        React.createElement("button", { className: recordView === "daily" ? "active" : "", onClick: () => setRecordView("daily") }, "1日"),
        React.createElement("button", { className: recordView === "week" ? "active" : "", onClick: () => setRecordView("week") }, "1週間"),
        React.createElement("button", { className: recordView === "calendar" ? "active" : "", onClick: () => setRecordView("calendar") }, "カレンダー")
      ),
      recordView === "daily" && renderDaily(),
      recordView === "week" && renderWeek(),
      recordView === "calendar" && renderCalendarLog()
    );
  }

  function renderWeek() {
    const data = weekData || { totals: emptyTotals, days: [], message: "今週はここからでOK。" };
    return React.createElement(
      React.Fragment,
      null,
      React.createElement(
        "section",
        { className: "sectionHead" },
        React.createElement("h2", null, "1週間"),
        React.createElement("p", null, data.start && data.end ? `${data.start} - ${data.end}` : "")
      ),
      React.createElement(
        "section",
        { className: "encouragement" },
        React.createElement("strong", null, data.message)
      ),
      React.createElement(
        "section",
        { className: "totals" },
        React.createElement(Stat, { label: "平均カロリー", value: (data.averages || data.totals).calories, unit: "kcal/日", tone: "wide" }),
        React.createElement(Stat, { label: "平均タンパク質", value: (data.averages || data.totals).protein, unit: "g/日" }),
        React.createElement(Stat, { label: "平均脂質", value: (data.averages || data.totals).fat, unit: "g/日" }),
        React.createElement(Stat, { label: "平均糖質", value: (data.averages || data.totals).sugar, unit: "g/日" }),
        React.createElement(Stat, { label: "平均食物繊維", value: (data.averages || data.totals).fiber, unit: "g/日" })
      ),
      React.createElement(
        "section",
        { className: "weekList" },
        data.days.map((day) =>
          React.createElement(
            "button",
            {
              key: day.date,
              className: day.meal_count > 0 ? "weekDay recorded" : "weekDay",
              onClick: () => {
                loadDay(day.date);
                setRecordView("daily");
                setView("record");
              },
            },
            React.createElement("strong", null, day.date.slice(5).replace("-", "/")),
            React.createElement("span", null, day.meal_count > 0 ? `${day.meal_count}食 ${day.totals.calories}kcal` : "休憩日")
          )
        )
      )
    );
  }

  function renderCalendarLog() {
    const maxDate = days[0]?.date || selectedDate;
    const minDate = days[days.length - 1]?.date || selectedDate;

    return React.createElement(
      React.Fragment,
      null,
      React.createElement(
        "section",
        { className: "calendarPicker" },
        React.createElement(
          "label",
          null,
          "日付",
          React.createElement("input", {
            type: "date",
            value: selectedDate,
            min: minDate,
            max: maxDate,
            onChange: (event) => loadDay(event.target.value),
          })
        )
      ),
      React.createElement(
        "section",
        { className: "sectionHead" },
        React.createElement("h2", null, "カレンダー"),
        React.createElement("p", null, selectedDate)
      ),
      renderSetupGuide(),
      renderTotals(),
      renderPfcGapGraph(),
      renderAiNotice(),
      renderMealList("この日の記録はありません")
    );
  }

  function renderReview() {
    return React.createElement(
      React.Fragment,
      null,
      React.createElement(
        "section",
        { className: "sectionHead reviewHead" },
        React.createElement("strong", { className: "streakHeadline" }, `${streak?.streak || 0}日継続中！`),
        React.createElement("h2", null, "レビュー"),
        React.createElement("p", null, selectedDate)
      ),
      renderEncouragement(),
      renderSetupGuide(),
      renderReviewBox()
    );
  }

  function renderSettings() {
    return React.createElement(
      "section",
      { className: "settingsPanel" },
      React.createElement("h2", null, "設定"),
      authUser &&
        React.createElement(
          "div",
          { className: "accountBox" },
          authUser.picture && React.createElement("img", { src: authUser.picture, alt: "" }),
          React.createElement(
            "div",
            null,
            React.createElement("strong", null, authUser.name || "LINEユーザー"),
            React.createElement("span", null, "LINEログイン中")
          ),
          React.createElement("button", { className: "ghost", onClick: logout }, "ログアウト")
        ),
      authRequired && !authUser &&
        React.createElement(
          "div",
          { className: "accountBox noAvatar" },
          React.createElement(
            "div",
            null,
            React.createElement("strong", null, "LINE認証"),
            React.createElement("span", null, "ログインすると端末を変えても記録を引き継げます")
          ),
          React.createElement("a", { className: "lineMiniButton", href: "/auth/line/login" }, "LINEでログイン")
        ),
      React.createElement(
        "label",
        null,
        "年齢",
        React.createElement("input", {
          type: "number",
          inputMode: "numeric",
          value: settings.age,
          onChange: (event) => setSetting("age", event.target.value),
        })
      ),
      React.createElement(
        "label",
        null,
        "体重 kg",
        React.createElement("input", {
          type: "number",
          inputMode: "decimal",
          value: settings.weight,
          onChange: (event) => setSetting("weight", event.target.value),
        })
      ),
      React.createElement(
        "label",
        null,
        "身長 cm",
        React.createElement("input", {
          type: "number",
          inputMode: "decimal",
          value: settings.height,
          onChange: (event) => setSetting("height", event.target.value),
        })
      ),
      React.createElement(
        "label",
        null,
        "性別",
        React.createElement(
          "select",
          { value: settings.sex, onChange: (event) => setSetting("sex", event.target.value) },
          React.createElement("option", { value: "" }, "未設定"),
          React.createElement("option", { value: "男性" }, "男性"),
          React.createElement("option", { value: "女性" }, "女性"),
          React.createElement("option", { value: "その他" }, "その他")
        )
      ),
      React.createElement(
        "label",
        null,
        "基礎代謝 kcal",
        React.createElement("input", {
          type: "number",
          inputMode: "numeric",
          value: settings.basal_metabolism,
          onChange: (event) => setSetting("basal_metabolism", event.target.value),
        })
      ),
      React.createElement(
        "label",
        null,
        "運動回数 / 週",
        React.createElement(
          "select",
          {
            value: settings.exercise_per_week,
            onChange: (event) => setSetting("exercise_per_week", event.target.value),
          },
          React.createElement("option", { value: "" }, "未設定"),
          React.createElement("option", { value: "0" }, "0回"),
          React.createElement("option", { value: "1" }, "1回"),
          React.createElement("option", { value: "2" }, "2回"),
          React.createElement("option", { value: "3" }, "3回"),
          React.createElement("option", { value: "4" }, "4回"),
          React.createElement("option", { value: "5" }, "5回"),
          React.createElement("option", { value: "6" }, "6回"),
          React.createElement("option", { value: "7" }, "7回以上")
        )
      ),
      React.createElement(
        "label",
        null,
        "目標体重 kg",
        React.createElement("input", {
          type: "number",
          inputMode: "decimal",
          value: settings.target_weight,
          onChange: (event) => setSetting("target_weight", event.target.value),
        })
      ),
      React.createElement(
        "label",
        null,
        "何週間で痩せたいか",
        React.createElement("input", {
          type: "number",
          inputMode: "numeric",
          value: settings.target_weeks,
          onChange: (event) => setSetting("target_weeks", event.target.value),
        })
      ),
      React.createElement(
        "label",
        null,
        "目的",
        React.createElement(
          "select",
          { value: settings.purpose, onChange: (event) => setSetting("purpose", event.target.value) },
          purposes.map((purpose) => React.createElement("option", { key: purpose, value: purpose }, purpose))
        )
      ),
      React.createElement(
        "label",
        null,
        "AIレビューの口調",
        React.createElement(
          "select",
          { value: settings.review_tone || "甘目", onChange: (event) => setSetting("review_tone", event.target.value) },
          reviewTones.map((tone) => React.createElement("option", { key: tone, value: tone }, tone))
        )
      ),
      renderProfileSummary(),
      React.createElement(
        "button",
        { className: "primary wideButton", onClick: saveSettings, disabled: settingsBusy },
        settingsBusy ? "保存中" : "保存"
      ),
      settingsMessage && React.createElement("p", { className: "formMessage" }, settingsMessage),
      renderPrivacyPanel(),
      renderFeedbackPanel()
    );
  }

  function renderLogin() {
    return React.createElement(
      "main",
      { className: "shell loginShell" },
      React.createElement(
        "section",
        { className: "loginPanel" },
        React.createElement("p", { className: "eyebrow" }, "Eatake"),
        React.createElement("h1", null, "撮るだけで記録"),
        React.createElement("p", null, "LINEでログインすると、あなたの食事ログを安全にクラウド保存できます。"),
        React.createElement("a", { className: "lineButton", href: "/auth/line/login" }, "LINEでログイン")
      )
    );
  }

  if (!authReady) {
    return React.createElement(
      "main",
      { className: "shell loginShell" },
      React.createElement("section", { className: "loginPanel" }, React.createElement("p", null, "読み込み中"))
    );
  }

  if (authRequired && !authUser) {
    return renderLogin();
  }

  return React.createElement(
    "main",
    { className: "shell" },
    React.createElement(
      "header",
      { className: "topbar" },
      React.createElement(
        "div",
        null,
        React.createElement("p", { className: "eyebrow" }, "Eatake"),
        React.createElement("h1", null, `${totals.calories} kcal`)
      ),
      React.createElement("button", { className: "ghost", onClick: refreshAll }, "更新")
    ),
    React.createElement(
      "nav",
      { className: "tabs" },
      React.createElement("button", { className: view === "camera" ? "active" : "", onClick: () => setView("camera") }, "記録"),
      React.createElement("button", { className: view === "record" ? "active" : "", onClick: () => setView("record") }, "履歴"),
      React.createElement("button", { className: view === "review" ? "active" : "", onClick: () => setView("review") }, "レビュー"),
      React.createElement("button", { className: view === "settings" ? "active" : "", onClick: () => setView("settings") }, "設定")
    ),
    view === "camera" && renderCamera(),
    view === "record" && renderRecord(),
    view === "review" && renderReview(),
    view === "settings" && renderSettings(),
    updateReady &&
      React.createElement(
        "div",
        { className: "updateBanner", role: "status" },
        React.createElement("span", null, "新しいバージョンがあります"),
        React.createElement("button", { className: "primary", onClick: reloadApp }, "更新")
      ),
    renderEditMealModal(),
    successBurst &&
      React.createElement(
        "div",
        { className: "successBurst", "aria-hidden": "true" },
        React.createElement(
          "div",
          { className: "dopamineBadge" },
          React.createElement("strong", null, "+1"),
          React.createElement("small", null, "記録できた")
        ),
        Array.from({ length: 24 }).map((_, index) => React.createElement("span", { key: index }))
      ),
    successText &&
      React.createElement(
        "div",
        { className: "successToast", role: "status", "aria-live": "polite" },
        React.createElement("strong", null, successText),
        React.createElement("span", null, streak?.streak >= 2 ? `${streak.streak}日連続、いい流れです` : "今日もちゃんと残せました")
      ),
    renderLoadingOverlay()
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(App));
