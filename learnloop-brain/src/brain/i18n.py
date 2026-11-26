"""User-facing texts in one catalog (Hebrew + English).

Rule: stored data holds a MESSAGE KEY and its parameters (language-neutral); text is rendered
in the user's language only when it is shown. Old records stored as plain text are shown as-is.
Parameters whose name ends with `_key` are themselves message keys and are translated too.
"""

from __future__ import annotations

DEFAULT_LANG = "he"     # [RECOMMENDATION] the user's language; settings.language overrides

STATE = {"new": ("חדש", "new"), "learning": ("בלמידה", "learning"),
         "understood": ("הובן", "understood"), "reinforce": ("לחיזוק", "reinforce")}
ACTIVITY = {"quiz": ("בוחן", "Quiz"), "game": ("משחק", "Game"), "project": ("פרויקט", "Project"),
            "summary": ("סיכום", "Summary"), "challenge": ("אתגר", "Challenge")}
EVIDENCE = {"quiz": ("בוחן", "quiz"), "explain": ("הסבר", "explain"), "apply": ("יישום", "apply"),
            "transfer": ("העברה", "transfer"), "retention": ("זכירה", "retention"),
            "debug": ("איתור שגיאה", "debug"), "analyze": ("ניתוח", "analyze"),
            "exposure": ("חשיפה", "exposure")}

M = {
    # ---- transparency summary
    "tr.new_topics": ("נושאים חדשים: {names}.", "New topics: {names}."),
    "tr.evidence": ("נוספו {n} ראיות ל-{subject}.", "{n} piece(s) of evidence were added to {subject}."),
    "tr.order": ("נקבע סדר לימוד: {items}.", "Learning order added: {items}."),
    "tr.not_changed": ("לא שונה: {items}.", "Not changed: {items}."),
    "tr.mastery": ("ההערכה עודכנה ({model_key}): {items}.", "Assessment updated ({model_key}): {items}."),
    "tr.mastery_same": ("הערכת השליטה לא השתנתה.", "The mastery assessment was not changed."),
    "tr.change": ("{topic}: {before_key} ← {after_key}", "{topic}: {before_key} -> {after_key}"),
    "tr.order_item": ("{src} לפני {dst}", "{src} -> {dst}"),
    "model.provisional": ("מודל זמני לבדיקה", "provisional test model"),
    "model.bayesian": ("נוסחה מאושרת", "approved formula"),
    "model.placeholder": ("ללא מודל", "no model"),
    "note.subject_rejected": ("השיוך למקצוע נדחה על ידך", "subject assignment rejected by the user"),
    "note.new_subject_rejected": ("המקצוע החדש נדחה על ידך – הגרף לא השתנה",
                                  "new subject rejected by the user – graph unchanged"),
    "note.source_removed": ("הקובץ הוסר – התוכן שלו כבר לא בשימוש; הראיות וההיסטוריה נשמרות",
                            "source removed – its content is no longer used; evidence and history are kept"),
    "note.nothing_to_remove": ("אין מה להסיר", "nothing to remove"),
    "skip.conflict": ("{topic}: סתירה – לא שונה", "{topic}: conflict – not changed"),
    "skip.rejected": ("{topic}: נדחה על ידך", "{topic}: rejected by the user"),
    "skip.large_rejected": ("{topic}: שינוי גדול שנדחה על ידך", "{topic}: large change rejected by the user"),
    "skip.order_contradicts": ("הסדר {src} לפני {dst} סותר סדר קיים", "order {src} -> {dst}: contradicts existing order"),
    "skip.order_setting": ("סדר עבור {topic} לא נוסף (ההגדרה דורשת אישור)",
                           "order for {topic}: not added (setting: needs approval)"),
    # ---- cycle summary
    "cycle.waiting": ("{n} פריטים ממתינים להחלטה שלך.", "{n} item(s) are waiting for your decision."),
    "cycle.failed": ("{n} פריטים לא עובדו וינוסו שוב.", "{n} item(s) could not be processed and will be tried again."),
    "cycle.nothing": ("לא השתנה דבר.", "Nothing changed."),
    # ---- approval requests
    "req.new_subject": ("זוהה מקצוע חדש: '{name}'", "New subject identified: '{name}'"),
    "req.low_subject": ("לא בטוח שהחומר שייך ל-'{name}'", "Unsure the material belongs to '{name}'"),
    "req.low_topics": ("לא בטוח לגבי הנושאים: {names}", "Unsure about topic(s): {names}"),
    "req.conflict": ("'{name}' הותאם לנושא של מקצוע אחר", "'{name}' was matched to a topic of another subject"),
    "req.major": ("{n} נושאים חדשים יתווספו בבת אחת", "{n} new topics would be added at once"),
    # ---- recommendations
    "rec.track_placement": ("מקצוע שכבר למדת: בחן רחב קצר כדי לראות מה נשאר איתך – בלי ללמוד מחדש מאפס.",
                            "A subject you already studied: a short broad quiz to see what stayed with you – no starting over."),
    "rec.track_project": ("אחרי הבחן הרחב: פעילות קצרה לבדיקת הבנה ויישום.",
                          "After the broad quiz: a short activity to check understanding and application."),
    "rec.track_refresh": ("רענון שבועי: בחן קצר, או בחירת פעילות אחרת שמתאימה לך.",
                          "Weekly refresher: a short quiz, or choose another activity that suits you."),
    "rec.continue": ("יש פעילות שלא הסתיימה – אפשר להמשיך בה.", "You have an unfinished activity – continue if you like."),
    "rec.foundation": ("'{pre}' הוא בסיס ל-'{topic}'.", "'{pre}' is a foundation for '{topic}'."),
    "rec.weak": ("התוצאות האחרונות מראות פער – פעילות קצרה וממוקדת.", "Recent results show a gap – a short targeted activity."),
    "rec.game": ("משחק קצר לחיזוק מה שכבר נלמד.", "A quick game to strengthen what you already learned."),
    "rec.learn": ("בוחן קצר כדי לראות מה כבר ברור.", "A short quiz to see what is already clear."),
    "rec.retention": ("עבר זמן – בדיקת זכירה קצרה.", "Some time has passed – a quick memory check."),
    "rec.transfer": ("הנושא ידוע היטב – אפשר לנסות אותו במצב חדש.", "You know this well – try it in a new situation."),
    # ---- feedback
    "fb.correct": ("נכון.", "Correct."),
    "fb.wrong": ("לא בדיוק. התשובה הנכונה: {answer}", "Not quite. Correct answer: {answer}"),
    "fb.match_all": ("כל הזוגות הותאמו.", "All pairs matched."),
    "fb.match_some": ("{k} מתוך {n} זוגות הותאמו.", "{k} of {n} pairs matched."),
    "hint.default": ("כדאי לחזור לרעיון המרכזי של הנושא.", "Look again at the main idea of this topic."),
    # ---- assessment
    "m.reason": ("[{model_key}] ציון {score} מ-{n} תוצאות ({kinds}); {why_key}",
                 "[{model_key}] score {score} from {n} result(s) ({kinds}); {why_key}"),
    "m.exposure": ("הנושא מופיע בחומר הלימוד – חשיפה בלבד, לא מניחים הבנה",
                   "topic appears in the study material – exposure only, no understanding assumed"),
    "m.retention": ("אין ראיה כבר {days} ימים – מוצעת בדיקת זכירה", "no evidence for {days} days – retention check suggested"),
    "m.understood": ("הראיות מגוונות וכוללות הצלחה עצמאית", "evidence is varied and includes an independent success"),
    "m.gap": ("התוצאות האחרונות מראות פער", "recent results show a gap"),
    "m.progress": ("יש התקדמות, עדיין אין מספיק ראיות", "progress, not yet enough evidence"),
    "guard.single": ("תוצאה אחת לעולם לא מספיקה", "a single result is never enough"),
    "guard.kinds": ("נדרשים לפחות שני סוגי ראיות", "needs at least two kinds of evidence"),
    "guard.independent": ("נדרשת לפחות הצלחה אחת בלי עזרה משמעותית", "needs at least one success without substantial help"),
    "mastery.updated": ("{cause_key}: {detail_key}", "{cause_key}: {detail_key}"),
    # ---- history reasons
    "cause.new_material": ("חומר לימוד חדש", "new study material"),
    "cause.activity_finished": ("{type_key} הסתיים", "{type_key} finished"),
    "h.topic_identified": ("זוהה בחומר הלימוד", "identified in study material"),
    "h.context_created": ("הקשר לימוד חדש (תיקייה + מקצוע)", "new study context (folder + subject)"),
    "h.context_from_setting": ("לפי הסיווג שהגדרת לתיקייה", "from the folder classification you set"),
    "h.context_user": ("לפי תשובתך לשאלת הסיווג", "from your answer to the classification question"),
    "h.classification_asked": ("נשאלה שאלת סיווג אחת לחומר הזה", "one classification question was asked"),
    "h.cycle_ran": ("מחזור עיבוד ({trigger_key})", "processing cycle ({trigger_key})"),
    "h.cycle_deferred": ("העיבוד נדחה – את באמצע לימוד", "processing postponed – you are studying"),
    "trigger.user_request": ("לבקשתך", "on your request"),
    "trigger.scheduled": ("מחזור קבוע כל שעתיים", "regular two-hour cycle"),
    "trigger.accumulation": ("הצטבר הרבה חומר", "a lot of material accumulated"),
    "notice.cycle_later": ("חומר חדש יעובד אחרי שתסיימי – לא נפריע לך עכשיו.",
                           "New material will be processed when you finish – we won't interrupt now."),
    "str.success": ("{n} נושאים הובנו ב-{subject}: {topics}", "{n} topics understood in {subject}: {topics}"),
    "str.ability": ("חוזקה ב{ability} ב-{subject} ({n} תשובות, ממוצע {avg}%)",
                    "strong at {ability} in {subject} ({n} answers, average {avg}%)"),
    "str.general": ("יכולת שחוזרת בכמה מקצועות: {ability} ({k} מקצועות, {n} תשובות)",
                    "an ability across subjects: {ability} ({k} subjects, {n} answers)"),
    "str.came_back": ("התמדה: '{topic}' חזר מחיזוק להבנה", "persistence: '{topic}' went from reinforcement to understood"),
    "str.progress": ("התקדמות ב-'{topic}': התוצאות עלו ב-{rise} נקודות", "progress in '{topic}': results rose {rise} points"),
    "rec.use_strength": ("נשען על החוזקה שלך ב{ability}.", "Builds on your strength in {ability}."),
    "rec.mistake_misconception": ("נראה שיש אי-הבנה בהגדרה – קודם סיכום קצר שמבהיר אותה.",
                                  "Looks like a misconception – first a short summary that clarifies it."),
    "rec.mistake_incomplete": ("התשובות היו חלקיות – אתגר קצר שמשלים את התמונה.",
                               "Answers were incomplete – a short challenge to complete the picture."),
    "rec.mistake_careless": ("הטעויות נראות כמו חוסר תשומת לב – משחק קצר ומהיר.",
                             "Mistakes look like slips – a quick game."),
    "h.skill_identified": ("מיומנות שזוהתה בחומר (חלק מהנושא)", "skill identified in material (part of the topic)"),
    "h.related_identified": ("נושאים קשורים לפי החומר", "related topics according to the material"),
    "m.historical_only": ("החומר סווג כחומר שהסתיים או שטרם נלמד – עדיין אין ראיות לשליטה",
                          "the material was classified as finished or not studied yet – no evidence of mastery yet"),
    "cause.classification": ("סיווג החומר השתנה", "the material classification changed"),
    "budget.exhausted": ("תקציב ה-AI של החודש נוצל. החומר נשמר בתור ויעובד בחודש הבא – שום דבר לא נמחק.",
                         "This month's AI budget is used up. Material stays queued for next month – nothing is deleted."),
    "h.activity_link": ("קישור אפליקציה למקצוע לפי תשובתך", "app linked to a subject by your answer"),
    "credit.exhausted": ("הקרדיט בחשבון של ספק ה-AI נגמר. השליחה ל-AI נעצרה והחומר נשמר בתור – שום דבר לא נמחק. אם תטעיני קרדיט, העיבוד יתחדש לבד.",
                         "The AI provider credit is used up. AI sends paused; the queue is kept. After a top-up processing resumes by itself."),
    "ai.not_ready": ("החומר התקבל ונשמר מוצפן במחשב שלך. הוא ימתין עד שספק AI יאושר ויוגדר – שום דבר לא נשלח.",
                     "Material received and stored encrypted on your computer. It waits until an AI provider is approved and configured – nothing was sent."),
    "h.ai_not_ready": ("העיבוד ממתין – ספק AI עדיין לא מאושר; החומר נשאר בתור", "processing waiting – no approved AI provider yet; queue kept"),
    "h.budget_paused": ("העיבוד נעצר – תקציב החודש נוצל; החומר נשאר בתור", "processing paused – monthly budget used; queue kept"),
    "study.completed": ("נלמד בעבר (הסתיים)", "studied before (finished)"),
    "study.active": ("נלמד עכשיו", "studying now"),
    "study.not_studied": ("טרם נלמד", "not studied yet"),
    "study.unknown": ("לא ידוע", "unknown"),
    "study.mixed": ("כמה תקופות", "several periods"),
    "assess.assessed": ("הוערך", "assessed"),
    "assess.not_assessed": ("טרם הוערך", "not assessed yet"),
    "h.evidence_material": ("ראיה מחומר הלימוד", "evidence from study material"),
    "h.evidence_activity": ("תשובה ב{type_key}", "{type_key} answer"),
    "h.order_material": ("הוצע מחומר הלימוד", "proposed from study material"),
    "h.order_late": ("הצעה קודמת – נושא הבסיס כבר ידוע", "earlier proposal, topic now known"),
    "h.subject_approved": ("מקצוע חדש שאישרת", "new subject approved by the user"),
    "h.content_received": ("חומר התקבל מה-Collector", "content received from the Collector"),
    "h.content_removed": ("הקובץ נמחק או הוצא מהתיקייה", "file deleted or moved out of the approved folder"),
    "h.user_decision": ("החלטה שלך", "decision by the user"),
    "h.permission": ("שינית את הרשאת ההעברה", "user changed transfer permission"),
    "h.activity_created": ("נוצר {type_key}", "{type_key} created"),
    "h.activity_finished": ("הפעילות הסתיימה", "activity finished"),
    "h.activity_skipped": ("דילגת – בלי השפעה על ההתקדמות", "skipped by the user – no effect on progress"),
    "h.activity_expired": ("לא הושלם במשך {days} ימים – בלי עונש", "unfinished for more than {days} days – no penalty"),
}


for _k, _pair in EVIDENCE.items():
    M["ev." + _k] = _pair


def label(table: dict, value, lang: str) -> str:
    pair = table.get(value)
    return pair[0 if lang == "he" else 1] if pair else str(value)


def t(key: str, lang: str | None = None, **params) -> str:
    lang = lang or DEFAULT_LANG
    if key in STATE:
        return label(STATE, key, lang)
    if key in ACTIVITY:
        return label(ACTIVITY, key, lang)
    pair = M.get(key)
    if pair is None:
        return key                                  # plain text (old records) – shown as-is
    rendered = {}
    for name, value in params.items():
        if name.endswith("_key"):
            value = t(value, lang) if not isinstance(value, dict) else render(value, lang)
        elif isinstance(value, (list, tuple)):
            value = ", ".join(render(v, lang) if isinstance(v, dict) else str(v) for v in value)
        rendered[name] = value
    return pair[0 if lang == "he" else 1].format(**rendered)


def msg(key: str, **params) -> dict:
    """A storable message: key + parameters."""
    return {"key": key, **params}


def render(message, lang: str | None = None) -> str:
    """Render a stored message (dict from msg()) or return plain text unchanged."""
    if isinstance(message, dict) and "key" in message:
        params = {k: v for k, v in message.items() if k != "key"}
        return t(message["key"], lang, **params)
    return "" if message is None else str(message)
