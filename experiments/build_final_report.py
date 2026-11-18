"""Build the academic report from verified evidence and reviewed selection."""
import json
from pathlib import Path
import sys
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def main():
    out = Path(sys.argv[1]).resolve()
    evidence = json.loads((out / "evidence.json").read_text())
    selection = json.loads((out / "selection.json").read_text())
    assert evidence["quality_gate"] == "PASS" and evidence["repetitions"] == 8
    metrics = evidence["metrics"]
    systems = ["static", "reactive", "predictive-baseline", "predictive-ml"]
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(.7)
    section.left_margin = section.right_margin = Inches(.75)
    for name in ("Normal", "Title", "Subtitle", "Heading 1", "Heading 2", "Caption"):
        style = doc.styles[name]
        style.font.name = "Arial"
        style.font.color.rgb = RGBColor(0, 0, 0)
    normal = doc.styles["Normal"]
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.line_spacing = 1.08
    doc.styles["Title"].font.size = Pt(27)
    doc.styles["Heading 1"].font.size = Pt(18)
    doc.styles["Heading 2"].font.size = Pt(13)

    def p(text):
        doc.add_paragraph(text)

    def h(text):
        doc.add_heading(text, 1)

    def table(headers, rows, widths):
        result = doc.add_table(rows=1, cols=len(headers))
        result.alignment = WD_TABLE_ALIGNMENT.CENTER
        result.autofit = False
        for index, width in enumerate(widths):
            result.columns[index].width = Inches(width)
        for index, text in enumerate(headers):
            result.rows[0].cells[index].text = text
        for row in rows:
            for cell, text in zip(result.add_row().cells, row):
                cell.text = str(text)
        for index, row in enumerate(result.rows):
            for col, cell in enumerate(row.cells):
                cell.width = Inches(widths[col])
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                props = cell._tc.get_or_add_tcPr()
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "243D53" if index == 0 else ("F2F5F7" if index % 2 else "FFFFFF"))
                props.append(shade)
                borders = OxmlElement("w:tcBorders")
                for edge in ("top", "left", "bottom", "right"):
                    part = OxmlElement("w:" + edge)
                    for attr, value in (("val", "single"), ("sz", "4"), ("color", "D9D9D9")):
                        part.set(qn("w:" + attr), value)
                    borders.append(part)
                props.append(borders)
                margins = OxmlElement("w:tcMar")
                for edge in ("top", "left", "bottom", "right"):
                    part = OxmlElement("w:" + edge)
                    part.set(qn("w:w"), "85")
                    part.set(qn("w:type"), "dxa")
                    margins.append(part)
                props.append(margins)
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_after = Pt(2)
                    paragraph.paragraph_format.space_before = Pt(2)
                    for run in paragraph.runs:
                        run.font.size = Pt(10)
                        run.bold = index == 0
                        run.font.color.rgb = RGBColor.from_string("FFFFFF" if index == 0 else "000000")
            if index == 0:
                row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        doc.add_paragraph().paragraph_format.space_after = Pt(1)

    def network_table(specs):
        rows = []
        for key, label in specs:
            values = [metrics[s][key]["Mean"] for s in systems]
            rows.append([label] + ["N/A" if value is None else f"{value:.3f}" for value in values])
        table(["Metric", "Static", "Reactive", "Persistence", "GB"], rows, [2.2, 1.1, 1.1, 1.3, 1.3])

    doc.add_paragraph("Predictive traffic engineering in software defined networks", "Title")
    doc.add_paragraph("Computer Networks project report\nSeptember 2026", "Subtitle")
    h("Abstract")
    p("We implemented a traffic engineering controller that forecasts link utilization five seconds ahead and changes the forwarding path when sustained congestion is expected. The system combines Ryu and OpenFlow with a four-switch Mininet testbed, a timestamp-aligned training pipeline, and a repeated network evaluation. The experiment compares Static routing, Reactive routing, Predictive Persistence and Predictive Gradient Boosting under the same workload.")
    p(selection["summary"])
    p("The final evidence contains eight fresh sessions per system, or 32 network runs. Results describe this deterministic workload and emulated topology. They do not establish production readiness, performance on arbitrary networks, or operational superiority over GRU, which was evaluated offline only.")
    h("Research question and contribution")
    p("The research question is whether a short-horizon utilization forecast changes network behavior enough to justify its runtime cost relative to a reactive controller and a persistence forecast. Our contribution is an auditable integration and comparison: exact-time targets, held-out model evaluation, live routing decisions, receiver measurements and repeated-run statistics are linked through preserved artifacts. We do not claim a new learning algorithm or a state-of-the-art result.")
    p("The operational comparison keeps the routing policy fixed so that the signal source is the principal controlled difference. Congestion duration is the main routing outcome, with tail RTT, delivered goodput, packet loss and CPU cost providing the associated tradeoffs.")

    doc.add_page_break()
    h("System implementation")
    table(["Component", "Implemented role"], [
        ["Mininet and Open vSwitch", "Two hosts and four OpenFlow 1.3 switches form a diamond with two candidate paths."],
        ["Ryu controller", "Polls counters, builds per-link history, obtains a routing signal and issues flow modifications."],
        ["Forecasting pipeline", "Validates real telemetry, prepares exact t+5 labels, purges split boundaries and evaluates saved models."],
        ["Experiment runner", "Creates a new network per run and captures ping, receiver UDP results, predictions, control events and CPU samples."],
    ], [1.7, 5.3])
    p("Path A follows h1, s1, s2, s4, h2. Its two constrained links each have 10 Mbps capacity and 2 ms configured delay. Path B follows h1, s1, s3, s4, h2 with 20 Mbps and 4 ms per constrained link. Path B therefore provides spare capacity at a higher propagation-delay cost.")
    p("The controller samples the two outgoing candidate links approximately once per second. It converts cumulative byte counters into throughput and utilization. The raw schema contains timestamp, link_id, utilization, throughput, delay, packet_loss and active_flows. The model receives 13 features after adding utilization lags, moving averages and slope. Eleven observations are required before inference begins.")
    p("The routing policy requires three consecutive congestion signals at or above 85 percent, at least five percentage points of projected improvement, a 70 percent recovery threshold and a 15-second cooldown. Alternative-path evaluation transfers the active traffic load to the alternative capacity before comparison, avoiding an assumption that an idle link will stay empty after rerouting.")
    h("Runtime safeguards")
    p("The frozen Gradient Boosting weights use a portable tree representation with a source checksum. Host parity testing covers 2,408 inputs with maximum absolute error below 1.1e-14 percentage points. The VM checks 38 valid fixture inputs through the prediction API. A full live smoke test precedes the campaign. Runs fail if controller errors occur, receiver results are absent or valid post-warmup predictions cover less than 95 percent of recorded eligible rows on either link.")

    doc.add_page_break()
    h("Calibration and offline model evaluation")
    p("Calibration 02 passed its preparation quality gate and supplied 1,470 accepted real telemetry rows across two links. Preparation excluded ten initial observations without nearby measured RTT and retained an explicit sampling-gap record. Exact timestamp joins attach utilization at t+5 seconds. Missing target timestamps are not replaced by the fifth subsequent row.")
    p("Feature preparation yielded 1,428 labelled rows. Chronological splitting with label-boundary purging produced 988 training, 204 validation and 216 test rows. Ten boundary observations were purged at each split boundary. Model selection and parameter tuning used validation data. Only three test targets exceeded the 85 percent congestion threshold.")
    table(["Model", "MAE", "RMSE", "R squared"], [[row["Model"], f"{float(row['MAE']):.4f}", f"{float(row['RMSE']):.4f}", f"{float(row['R2']):.4f}"] for row in evidence["model_metrics"]], [2.5, 1.5, 1.5, 1.5])
    p("MAE and RMSE are measured in utilization percentage points. GRU has the lowest regression error on this split. Gradient Boosting correctly classified all three congestion-positive test observations, with no false positives, while the other models missed these three cases. The resulting Gradient Boosting F1 of 1.0000 is based on very few positives and is not evidence of robust general congestion detection.")
    p("The predictive campaign candidate is a scikit-learn Gradient Boosting Regressor, despite historical filenames containing xgb. It contains 100 depth-two estimators with learning rate 0.03 and high-load sample weight 20. We held this model fixed throughout network evaluation. GRU has no live controller integration in this comparison, so its networking performance remains unknown.")
    h("Workload and replication")
    table(["Stage", "Offered rate", "Duration"], [["Warmup", "3 Mbps", "15 s"], ["Ramp", "7 Mbps", "15 s"], ["Near threshold", "9 Mbps", "15 s"], ["Overload", "13 Mbps", "25 s"], ["Recovery", "5 Mbps", "20 s"]], [3, 2, 2])
    p("Each of the 32 sessions uses this 90-second UDP sequence with identical setup and settling periods. Systems run in the specified fixed order. The four system groups each contain eight sessions, but not eight distinct workload distributions.")

    doc.add_page_break()
    h("Network performance results")
    p("The table reports the mean of eight per-run measurements. Persistence denotes the predictive persistence controller and GB denotes the predictive Gradient Boosting controller. The accompanying statistics files retain every per-run value, sample standard deviation and two-sided 95 percent Student t interval.")
    network_table([("Average_Latency_ms", "Average RTT ms"), ("P95_Latency_ms", "P95 RTT ms"), ("Average_Throughput_Mbps", "Receiver goodput Mbps"), ("Average_Packet_Loss_pct", "UDP loss percent"), ("Average_Jitter_ms", "Receiver jitter ms"), ("Mean_Controller_CPU_pct", "Controller CPU percent")])
    p(selection["qos_interpretation"])
    doc.add_picture(str(out / "graphs/p95_latency.png"), width=Inches(6.7))
    p("Latency uses successful end-to-end ping RTT observations during monitoring, including short workload margins. Goodput uses total received application bytes divided by total receiver segment duration. Packet loss is packet-weighted, and jitter is a duration-weighted mean of the receiver end-of-segment estimates. Missing ping replies do not enter the latency distribution.")

    doc.add_page_break()
    h("Routing outcomes and model selection")
    network_table([("Congestion_Duration_seconds", "Congestion seconds"), ("Congestion_Events", "Congestion events"), ("Reroutes", "Total reroutes"), ("Successful_Reroutes", "Successful relief actions"), ("Recovery_Reroutes", "Recovery actions"), ("False_Reroutes", "False congestion actions"), ("Congestion_Avoidance_Rate", "Episode avoidance fraction"), ("Prediction_to_Reroute_ms", "Prediction to decision ms"), ("Mean_Inference_ms", "Mean inference ms")])
    p(selection["routing_interpretation"])
    p(selection["decision"])
    h("Meaning of the routing metrics")
    p("Congestion duration counts sampled active-link utilization at or above 85 percent. Successful relief actions are A-to-B switches associated with a high matched-static reference within five seconds and ten subsequent seconds below threshold in the rerouted session. Recovery switches are separate. Episode avoidance is stricter: the comparison session must have no high samples over the paired static episode. A controller can therefore relieve an episode without avoiding it.")
    p("These are time-aligned experimental proxies, not causal proof of prevented incidents. Prediction-to-decision timing excludes flow-installation acknowledgement. Static and Reactive inference fields are not applicable. CPU uses interval process ticks, with 100 percent representing one logical core.")

    doc.add_page_break()
    h("Limitations and reproducibility")
    p("The principal limitation is workload diversity. The testbed has one topology, one source-destination pair and one deterministic high-load sequence. Eight fresh sessions measure repeatability but do not establish performance under new traffic patterns. Fixed system ordering can confound time-of-run effects. VM scheduling and competing host activity also influence timing.")
    p("Calibration has only three congestion-positive test targets. The same workload family informs calibration and evaluation, although campaign data is never used to refit the model. Row-based feature history can span sampling gaps. One-second measurements may miss subsecond congestion, and controller event timestamps do not certify hardware flow acknowledgement.")
    p("Live delay uses the latest causal ping RTT no more than 1.5 seconds old. Calibration used the nearest covered RTT within 1.5 seconds, so the alignment is not identical. Delay has small but nonzero importance in the frozen model. Training packet loss and live drop counters also differ semantically, but this frozen ensemble has no split on packet loss or active flows. New models using those fields require a unified measurement contract.")
    p("The original campaign 01 and first repaired smoke capture remain excluded because inference failed. Their files are preserved as diagnostics. Final analysis uses receiver interval counters to work around the older iperf server end-summary byte field. A manifest records raw-file and analysis-source checksums. Confidence intervals use sessions as observations, not individual packets.")
    h("Implementation demonstration")
    p("DEMO.md describes both a recorded replay on macOS and live Linux runs. The replay checks stored capture quality and displays actual predictions, path changes and network outcomes. The live runner creates real Mininet traffic and OpenFlow decisions. Recorded replay is explicitly labelled and is not presented as a live network.")
    h("Evidence index")
    p("Network captures: final-evaluation-artifacts/campaign-02. Offline comparison: cnt-ml/results/real-calibration-02. Final aggregates: statistics/network_statistics.csv and statistics/network_metrics_per_run.csv. Paired contrasts: statistics/paired_differences.csv. Measurement contract: experiments/METRIC_DEFINITIONS.md. Source checksums: source_manifest.json. Runtime code: path_switch_controller.py and telemetry_runtime.py.")
    p("The next research extension should test multiple burst timings and offered-load profiles with randomized run order, more independently generated congestion episodes, and a common feature contract before adding GRU to live routing. This is future validation, not a claim made by the completed prototype.")
    destination = out / "SDN_Project_Report.docx"
    if destination.exists():
        raise FileExistsError(destination)
    doc.save(destination)
    print(destination)


if __name__ == "__main__":
    main()
