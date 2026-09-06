import os
import sys
import json
import tempfile

def main():
    property_id = os.environ.get("GA_PROPERTY_ID", "").strip()
    credentials_json = os.environ.get("GA_CREDENTIALS", "").strip()

    if not property_id or not credentials_json:
        print("HATA: GA_PROPERTY_ID veya GA_CREDENTIALS secret eksik!")
        sys.exit(1)

    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json", encoding="utf-8") as tf:
        tf.write(credentials_json)
        temp_creds_path = tf.name

    try:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_creds_path
        client = BetaAnalyticsDataClient()

        request = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name="eventName")],
            metrics=[Metric(name="eventCount")],
            date_ranges=[DateRange(start_date="2026-01-01", end_date="today")],
        )

        response = client.run_report(request)
        events = {}
        for row in response.rows:
            e_name = row.dimension_values[0].value
            count = int(row.metric_values[0].value)
            events[e_name] = count

        print("GA4 Gelen Veriler:", events)

        # 4 Ana Metriği GA4'ten Hesapla
        ga_dl = max(
            events.get("file_download", 0),
            events.get("app_download", 0),
            events.get("web_download_click", 0)
        )
        ga_cut = max(
            events.get("batch_render_complete", 0),
            events.get("desktop_render_complete", 0)
        )
        ga_vis = max(
            events.get("page_view", 0),
            events.get("first_visit", 0)
        )

        final_dl = max(7, ga_dl)
        final_cut = max(15, ga_cut)
        final_vis = max(94, ga_vis)
        final_hours = max(3, round(final_cut * 0.15))

        new_stats = {
            "total_downloads": final_dl,
            "videos_cut": final_cut,
            "hours_saved": final_hours,
            "total_visitors": final_vis
        }

        with open("stats.json", "w", encoding="utf-8") as f:
            json.dump(new_stats, f, indent=2)

        print(f"BAŞARILI: stats.json GA4 ile eşitlendi -> {new_stats}")

    finally:
        if os.path.exists(temp_creds_path):
            os.remove(temp_creds_path)

if __name__ == "__main__":
    main()
