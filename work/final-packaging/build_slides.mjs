import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { Presentation, PresentationFile } from '@oai/artifact-tool';

const root = '/Users/mohit/College/SDN CP';
const out = path.resolve(process.argv[2]);
const evidence = JSON.parse(await fs.readFile(path.join(out, 'evidence.json'), 'utf8'));
const decision = JSON.parse(await fs.readFile(path.join(out, 'selection.json'), 'utf8'));
const skill = '/Users/mohit/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations';
const python = '/Users/mohit/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3';
const {resolvePresentationFont, applyPresentationChartFont, finalizePresentation} = await import(pathToFileURL(path.join(skill, 'container_tools/artifact_tool_utils.mjs')));
const family = resolvePresentationFont({fontFamily: 'Arial'});
const deck = Presentation.create({slideSize:{width:1280,height:720}});
const systems = ['static','reactive','predictive-baseline','predictive-ml'];
const labels = ['Static','Reactive','Persistence','Gradient Boosting'];
const metrics = evidence.metrics;
const privateDir = path.join(root, 'work/final-packaging/rendered');
await fs.mkdir(privateDir, {recursive:true});

function text(slide, value, top, size=29, height=100, bold=false, color='#253B50') {
  const shape = slide.shapes.add({geometry:'textbox',position:{left:72,top,width:1136,height},fill:'none',line:{fill:'none',width:0}});
  shape.text = value;
  shape.text.style = {typeface:family,fontSize:size,bold,color,autoFit:'none'};
  return shape;
}
function slide(title, notes) {
  const s=deck.slides.add(); s.background.fill='#FFFFFF';
  text(s,title,48,44,100,true);
  s.speakerNotes.textFrame.setText(notes);
  return s;
}
function table(s, values, widths, top=180, height=330) {
  const t=s.tables.add({rows:values.length,columns:values[0].length,left:72,top,width:1136,height,columnWidths:widths,values});
  t.borders.assign({style:'solid',fill:'#D9D9D9',width:1});
  for(let r=0;r<values.length;r++) for(let c=0;c<values[0].length;c++) {
    const cell=t.getCell(r,c); cell.fill=r===0?'#253B50':r%2?'#F0F4F7':'#FFFFFF';
    cell.text.style={typeface:family,fontSize:25,bold:r===0,color:r===0?'#FFFFFF':'#253B50'};
  }
  return t;
}
function value(system,metric){const n=metrics[system][metric].Mean;return Number.isFinite(n)?n.toFixed(2):'N/A';}
function bar(s,metric,unit) {
  const chart=s.charts.add('bar',{position:{left:100,top:165,width:1080,height:385},categories:labels,
    series:[{name:unit,values:systems.map(s=>metrics[s][metric].Mean),fill:'#315A91',valuesFormatCode:'0.00'}],
    barOptions:{direction:'column',grouping:'clustered',gapWidth:90},hasLegend:false,
    dataLabels:{showValue:true,position:'outEnd',textStyle:{fontSize:24}},
    yAxis:{min:0,title:unit,numberFormatCode:'0.0',textStyle:{fontSize:23}},
    xAxis:{textStyle:{fontSize:23}},chartFill:'#FFFFFF'});
  applyPresentationChartFont(chart,{fontFamily:family});
  text(s,'Means of eight fresh sessions per system. Full 95% intervals and per-run values accompany the report.',592,25,72);
}

let s=deck.slides.add();s.background.fill='#F1F4F6';
text(s,'Predictive SDN\ntraffic engineering',170,65,205,true);
text(s,'Computer Networks project\nImplementation and controlled network evaluation',424,31,110);
s.speakerNotes.textFrame.setText('Final campaign 02. Evidence: final-deliverables/evidence.json and source_manifest.json. No operational GRU comparison is claimed.');

s=slide('Network implementation','Source: experiments/run_experiment.py build_network, path_switch_controller.py. Delays are configured link delays, not measured RTT.');
table(s,[['Route','Switch sequence','Capacity','One-way link delay'],['Path A','s1, s2, s4','10 Mbps','4 ms'],['Path B','s1, s3, s4','20 Mbps','8 ms']],[185,351,200,400],180,230);
text(s,'Two hosts exchange UDP traffic through four OpenFlow switches. Ryu measures the candidate links and changes the forwarding path.',455,31,145);

s=slide('Forecasting and routing policy','Source: telemetry_runtime.py FeatureHistory, ReroutePolicy and project_path_signals; cnt-ml/src/build_features.py. Sampling gaps remain disclosed.');
text(s,'One-second telemetry supplies utilization, throughput, measured RTT and counter-derived fields. Eleven observations provide history for the 13-feature model.',172,30,145);
text(s,'The model predicts utilization five seconds ahead. Training labels use exact timestamps and chronological splits with boundary purging.',333,30,130);
text(s,'Routing requires three consecutive signals above 85%, projected improvement, a 70% recovery threshold and a 15-second cooldown.',478,30,135);

s=slide('Offline forecasting results','Source: cnt-ml/results/real-calibration-02/metrics_comparison.csv and congestion_metrics.csv. MAE/RMSE in utilization percentage points. GRU evaluated offline only.');
table(s,[['Model','MAE','RMSE','R squared'],...evidence.model_metrics.map(r=>[r.Model,Number(r.MAE).toFixed(3),Number(r.RMSE).toFixed(3),Number(r.R2).toFixed(3)])],[455,227,227,227],175,330);
text(s,'GRU has the lowest regression error. GB detected all three positive test cases, but three cases cannot establish robust congestion detection.',545,27,112);

s=slide('Controlled network experiment','Source: campaign-02 metadata workload and experiment provenance. Eight fresh sessions per system, fixed system order, no campaign data used to refit.');
table(s,[['Stage','Offered rate','Duration'],['Warmup','3 Mbps','15 s'],['Ramp','7 Mbps','15 s'],['Near threshold','9 Mbps','15 s'],['Overload','13 Mbps','25 s'],['Recovery','5 Mbps','20 s']],[536,300,300],160,350);
text(s,'Static, Reactive, Predictive Persistence and Predictive GB each run eight times with the same topology and policy.',551,28,104);

s=slide('Tail round-trip latency','Source: final-deliverables/statistics/network_statistics.csv, P95_Latency_ms. Bars show means, not confidence intervals. Intervals in companion graphs and report statistics.');
bar(s,'P95_Latency_ms','P95 RTT (ms)');
s=slide('Delivered UDP traffic','Source: final-deliverables/statistics/network_statistics.csv, Average_Throughput_Mbps. Goodput calculated from receiver intervals, not sender rate.');
bar(s,'Average_Throughput_Mbps','Receiver goodput (Mbps)');

s=slide('Congestion and reroute outcomes','Source: final-deliverables/statistics/network_statistics.csv. Definitions in experiments/METRIC_DEFINITIONS.md. Recovery actions excluded from false congestion reroutes.');
const keys=[['Congestion_Duration_seconds','Congestion seconds'],['Reroutes','Total reroutes'],['Recovery_Reroutes','Recovery actions'],['False_Reroutes','False congestion actions'],['Congestion_Avoidance_Rate','Episode avoidance fraction']];
table(s,[['Metric','Static','Reactive','Persistence','GB'],...keys.map(([key,label])=>[label,...systems.map(system=>value(system,key))])],[380,189,189,189,189],160,350);
text(s,'Episode avoidance requires no high samples during a matched static congestion episode. Relief after onset does not count as avoidance.',548,26,112);

s=slide('Final model decision','Source: final-deliverables/selection.json reviewed against campaign 02 network evidence and paired differences. Decision applies to this workload only.');
text(s,decision.slide_decision,180,35,180,true);
text(s,decision.slide_tradeoff,390,29,170);

s=slide('Limits of the result','Source: experiments/METRIC_DEFINITIONS.md; calibration provenance; archived offline comparison. No causal or production-generalization claims.');
text(s,'One topology, one host pair and one deterministic workload limit generalization. Fixed run order can confound time effects.',170,29,115);
text(s,'Only three congestion-positive offline test examples support the GB classification result. GRU has no live routing comparison.',305,29,115);
text(s,'One-second sampling and RTT alignment differ from packet-level timing. Counterfactual avoidance metrics remain reference proxies.',440,29,115);
text(s,'Eight-run confidence intervals describe repeatability in this emulator.',590,27,65);

s=slide('Implementation demonstration','Source: DEMO.md. Recorded replay command: .venv-ml/bin/python experiments/replay_demo.py --campaign final-evaluation-artifacts/campaign-02. Live commands run in Linux only.');
text(s,'The recorded replay shows verified telemetry, model outputs, path changes and receiver outcomes from the captured runs.',176,31,120);
text(s,'The live Linux runner creates Mininet traffic and OpenFlow reroutes. Each system can be demonstrated separately in about two minutes.',335,31,120);
text(s,'The report, editable results slides, raw captures and reproduction guide accompany the code.',502,31,105);

const candidate=path.join(privateDir,'candidate.pptx');
await (await PresentationFile.exportPptx(deck)).save(candidate);
for(let i=0;i<11;i++) {
  const slide=deck.slides.getItem(i);
  const preview=await deck.export({slide,format:'png',scale:1});
  await fs.writeFile(path.join(privateDir,`slide-${i+1}.png`),new Uint8Array(await preview.arrayBuffer()));
}
await finalizePresentation({workspaceDir:root,candidatePath:candidate,finalPath:path.join(out,'SDN_Project_Presentation.pptx'),
  pythonExecutable:python,integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit'],
  explicitTotalSlideCount:11,requiredNativeTableOwnerSlides:[2,4,5,8],requiredNativeChartOwnerSlides:[6,7],
  materializeLiteralChartWorkbooks:true,fontPolicy:{basis:'design',families:[family]},verifyArtifactToolImport:true,
  receiptPath:path.join(privateDir,'validation.json')});
console.log('Exported checked presentation');
