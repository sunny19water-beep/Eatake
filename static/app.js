const { useEffect, useMemo, useRef, useState } = React;

const emptyTotals = { calories: 0, protein: 0, fat: 0, carbs: 0, fiber: 0, sugar: 0, salt: 0 };
const defaultSettings = { age: "", weight: "", height: "", sex: "", purpose: "健康維持" };

function Stat({ label, value, unit, tone }) {
  return React.createElement(
    "div",
    { className: `stat ${tone || ""}` },
    React.createElement("span", null, label),
    React.createElement("strong", null, value),
    React.createElement("small", null, unit)
  );
}

function MealItem({ meal, onDelete }) {
  return React.createElement(
    "article",
    { className: "meal" },
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
    React.createElement("button", { onClick: () => onDelete(meal.id), className: "delete" }, "取消")
  );
}

function App() {
  const [view, setView] = useState("camera");
  const [authReady, setAuthReady] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);
  const [authUser, setAuthUser] = useState(null);
  const [selectedDate, setSelectedDate] = useState("");
  const [totals, setTotals] = useState(emptyTotals);
  const [meals, setMeals] = useState([]);
  const [days, setDays] = useState([]);
  const [review, setReview] = useState(null);
  const [streak, setStreak] = useState(null);
  const [weeklySummary, setWeeklySummary] = useState(null);
  const [weekData, setWeekData] = useState(null);
  const [settings, setSettings] = useState(defaultSettings);
  const [purposes, setPurposes] = useState(["ダイエット", "増量", "健康維持", "減量"]);
  const [cameraOn, setCameraOn] = useState(false);
  const [captureMode, setCaptureMode] = useState("meal");
  const [busy, setBusy] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [message, setMessage] = useState("写真を撮るだけで記録します");
  const [settingsMessage, setSettingsMessage] = useState("");
  const [quickText, setQuickText] = useState("");
  const [tapCount, setTapCount] = useState(0);
  const [lastRecordTaps, setLastRecordTaps] = useState(null);
  const [preview, setPreview] = useState("");
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
    return () => stopCamera();
  }, []);

  async function refreshAll() {
    const authRes = await fetch("/api/auth/me");
    const authData = await authRes.json();
    setAuthRequired(Boolean(authData.auth_required));
    setAuthUser(authData.user || null);
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
    setTotals(todayData.totals || emptyTotals);
    setMeals(todayData.meals || []);
    setReview(todayData.review || null);
    setStreak(todayData.streak || null);
    setWeeklySummary(todayData.weekly_summary || null);
    setWeekData(weekJson);
    setDays(daysData.days || []);
    setSettings(settingsData.settings || defaultSettings);
    setPurposes(settingsData.purposes || purposes);
  }

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    setAuthUser(null);
    setAuthRequired(true);
  }

  async function loadDay(day) {
    if (!day) return;
    const response = await fetch(`/api/days/${day}`);
    const data = await response.json();
    setSelectedDate(data.date);
    setTotals(data.totals || emptyTotals);
    setMeals(data.meals || []);
    setReview(data.review || null);
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
    setBusy(true);
    setMessage(captureMode === "label" ? "栄養成分表示を読み取っています" : "Geminiで推定しています");
    const formData = new FormData();
    formData.append("file", blob, filename);

    try {
      const endpoint = captureMode === "label" ? "/api/analyze-label" : "/api/analyze";
      const response = await fetch(endpoint, { method: "POST", body: formData });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "解析に失敗しました");
      setSelectedDate(data.meal.date);
      setTotals(data.totals);
      setMeals((current) => [data.meal, ...current.filter((meal) => meal.id !== data.meal.id)]);
      setDays(data.days || days);
      setReview(null);
      setLastRecordTaps(tapCount + 1);
      setTapCount(0);
      setMessage(captureMode === "label" ? "栄養成分表示の数値を採用しました" : "今日の合計に自動加算しました");
    } catch (error) {
      setMessage(error.message);
    } finally {
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
      setMeals(data.meals);
      setDays(data.days || days);
      setReview(null);
      setMessage("取り消しました");
    }
  }

  async function createReview() {
    setTapCount((count) => count + 1);
    setReviewBusy(true);
    try {
      const response = await fetch(`/api/days/${selectedDate}/review`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "レビュー作成に失敗しました");
      setReview(data.review);
    } catch (error) {
      setReview({ text: error.message, created_at: "" });
    } finally {
      setReviewBusy(false);
    }
  }

  async function saveSettings() {
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
      setPurposes(data.purposes || purposes);
      setSettingsMessage("保存しました");
    } catch (error) {
      setSettingsMessage(error.message);
    } finally {
      setSettingsBusy(false);
    }
  }

  async function saveQuickText() {
    const text = quickText.trim();
    if (!text) return;
    setBusy(true);
    setTapCount((count) => count + 1);
    try {
      const response = await fetch("/api/analyze-text", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "てきとう記録に失敗しました");
      setQuickText("");
      setSelectedDate(data.meal.date);
      setTotals(data.totals);
      setMeals((current) => [data.meal, ...current.filter((meal) => meal.id !== data.meal.id)]);
      setDays(data.days || days);
      setReview(null);
      setLastRecordTaps(1);
      setTapCount(0);
      setMessage("てきとう記録、ちゃんと残せました");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
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
      lastRecordTaps && React.createElement("small", null, `記録完了まで ${lastRecordTaps} タップ`)
    );
  }

  function renderQuickRecord() {
    return React.createElement(
      "section",
      { className: "quickRecord" },
      React.createElement("label", null, "てきとう記録"),
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

  function renderReviewBox() {
    return React.createElement(
      "section",
      { className: "reviewBox" },
      React.createElement(
        "div",
        null,
        React.createElement("h2", null, "AIレビュー"),
        React.createElement("p", null, review ? review.text : "1日の記録を確定すると、設定に合わせてレビューします")
      ),
      React.createElement(
        "button",
        { className: "primary", onClick: createReview, disabled: reviewBusy },
        reviewBusy ? "作成中" : "確定"
      )
    );
  }

  function renderMealList(emptyText) {
    return React.createElement(
      "section",
      { className: "history" },
      meals.length === 0 && React.createElement("p", { className: "empty" }, emptyText),
      meals.map((meal) => React.createElement(MealItem, { key: meal.id, meal, onDelete: deleteMeal }))
    );
  }

  function renderCamera() {
    return React.createElement(
      React.Fragment,
      null,
      renderEncouragement(),
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
          "成分表示"
        )
      ),
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
              className: "shutter",
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
      renderTotals(),
      renderQuickRecord()
    );
  }

  function renderDaily() {
    return React.createElement(
      React.Fragment,
      null,
      React.createElement(
        "section",
        { className: "sectionHead" },
        React.createElement("h2", null, "1日の記録"),
        React.createElement("p", null, selectedDate)
      ),
      renderTotals(),
      renderReviewBox(),
      renderEncouragement(),
      renderMealList("この日の記録はまだありません")
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
        React.createElement(Stat, { label: "週間カロリー", value: data.totals.calories, unit: "kcal", tone: "wide" }),
        React.createElement(Stat, { label: "タンパク質", value: data.totals.protein, unit: "g" }),
        React.createElement(Stat, { label: "脂質", value: data.totals.fat, unit: "g" }),
        React.createElement(Stat, { label: "糖質", value: data.totals.sugar, unit: "g" }),
        React.createElement(Stat, { label: "食物繊維", value: data.totals.fiber, unit: "g" })
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
                setView("daily");
              },
            },
            React.createElement("strong", null, day.date.slice(5).replace("-", "/")),
            React.createElement("span", null, day.meal_count > 0 ? `${day.meal_count}食 ${day.totals.calories}kcal` : "休憩日")
          )
        )
      )
    );
  }

  function renderLogs() {
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
        { className: "dateRail" },
        days.map((day) =>
          React.createElement(
            "button",
            {
              key: day.date,
              className: day.date === selectedDate ? "dateChip active" : "dateChip",
              onClick: () => loadDay(day.date),
            },
            React.createElement("strong", null, day.date.slice(5).replace("-", "/")),
            React.createElement("span", null, `${day.meal_count}食 ${day.calories}kcal`)
          )
        )
      ),
      React.createElement(
        "section",
        { className: "sectionHead" },
        React.createElement("h2", null, "これまでのログ"),
        React.createElement("p", null, selectedDate)
      ),
      renderTotals(),
      renderReviewBox(),
      renderMealList("この日の記録はありません")
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
        "目的",
        React.createElement(
          "select",
          { value: settings.purpose, onChange: (event) => setSetting("purpose", event.target.value) },
          purposes.map((purpose) => React.createElement("option", { key: purpose, value: purpose }, purpose))
        )
      ),
      React.createElement(
        "button",
        { className: "primary wideButton", onClick: saveSettings, disabled: settingsBusy },
        settingsBusy ? "保存中" : "保存"
      ),
      settingsMessage && React.createElement("p", { className: "formMessage" }, settingsMessage)
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
        React.createElement("p", { className: "eyebrow" }, "PFC Camera"),
        React.createElement("h1", null, `${totals.calories} kcal`)
      ),
      React.createElement("button", { className: "ghost", onClick: refreshAll }, "更新")
    ),
    React.createElement(
      "nav",
      { className: "tabs" },
      React.createElement("button", { className: view === "camera" ? "active" : "", onClick: () => setView("camera") }, "撮影"),
      React.createElement("button", { className: view === "daily" ? "active" : "", onClick: () => setView("daily") }, "1日"),
      React.createElement("button", { className: view === "week" ? "active" : "", onClick: () => setView("week") }, "1週間"),
      React.createElement("button", { className: view === "logs" ? "active" : "", onClick: () => setView("logs") }, "ログ"),
      React.createElement("button", { className: view === "settings" ? "active" : "", onClick: () => setView("settings") }, "設定")
    ),
    view === "camera" && renderCamera(),
    view === "daily" && renderDaily(),
    view === "week" && renderWeek(),
    view === "logs" && renderLogs(),
    view === "settings" && renderSettings()
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(App));
