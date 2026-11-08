import {useState} from "react";
import {ArrowUpRight, Copy, Download, Film, FolderOpen} from "lucide-react";
import {Button} from "./ui";
import {api, getBackend, inTauri, openExternal} from "../lib/api";
import {pickSavePath} from "../lib/dialog";
import {errorMessage, useInterval, useLoad} from "../lib/hooks";
import {t} from "../lib/i18n";

interface Session {id:string;name:string;first:string;last:string;prompt:string;folder:string;status:string;error:string|null;frames?:number}
interface FlowStatus {tools_ready:boolean;sessions:Session[];url:string}
const initialPrompt="Seamless continuous cinematic shot between the two reference frames. Start exactly from the first frame. Slowly move the camera forward with natural parallax, preserving the subject, materials and environment. Continue smoothly until the composition matches the second reference frame exactly, then gently settle. No cuts, no dissolve, no morphing, no teleportation, no sudden transition. No added text or logos.";
const field="w-full rounded-md border border-line bg-bg p-2 text-sm text-ink";

export function GoogleFlowPanel(){
 const status=useLoad(()=>api.get<FlowStatus>("/api/videos/flow"));
 const [name,setName]=useState(""),[first,setFirst]=useState(""),[last,setLast]=useState("");
 const [prompt,setPrompt]=useState(initialPrompt),[selected,setSelected]=useState("");
 const [video,setVideo]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
 const session=status.data?.sessions.find(s=>s.id===selected)??status.data?.sessions[0];
 const processing=status.data?.sessions.some(s=>s.status==="processing");
 useInterval(status.reload,processing?1500:null);
 async function action(fn:()=>Promise<unknown>){setBusy(true);setError("");setNotice("");try{await fn();status.reload();}catch(e){setError(errorMessage(e));}finally{setBusy(false);}}
 async function choose(kind:"first"|"last"|"video"){
  if(!inTauri)return;
  const {open}=await import("@tauri-apps/plugin-dialog");
  const path=await open({multiple:false,filters:[{name:kind==="video"?"MP4":"Images",extensions:kind==="video"?["mp4"]:["png","jpg","jpeg","webp"]}]});
  if(typeof path==="string")(kind==="first"?setFirst:kind==="last"?setLast:setVideo)(path);
 }
 async function download(){if(!session)return;
  if(inTauri){const path=await pickSavePath("frames_150.zip","zip",t("Save frame sequence"));if(path){await api.post(`/api/videos/flow/${session.id}/save`,{path});setNotice(t("Frame sequence saved to {path}",{path}));}return;}
  const backend=await getBackend(),response=await fetch(`${backend.url}/api/videos/flow/${session.id}/sequence`,{headers:backend.token?{Authorization:`Bearer ${backend.token}`}:{}});
  if(!response.ok)throw new Error(t("The frame sequence is not ready."));
  const url=URL.createObjectURL(await response.blob()),link=document.createElement("a");link.href=url;link.download="frames_150.zip";link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
 }
 const labels={prepared:t("Ready for Flow"),processing:t("Extracting frames…"),ready:t("Sequence ready"),failed:t("Failed")};
 return <section className="grid gap-4 rounded-xl border border-accent/40 bg-surface p-5" aria-labelledby="flow-heading">
  <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 id="flow-heading" className="flex items-center gap-2 text-lg font-semibold"><Film size={20}/>{t("Google Flow · Pro credits")}</h2><p className="mt-1 max-w-3xl text-sm text-ink-2">{t("Prepare two frames here, generate the video in your Flow browser, then import the downloaded MP4. Frame extraction runs on this PC.")}</p></div><Button icon={<ArrowUpRight size={16}/>} onClick={()=>void action(()=>openExternal("https://flow.google.com/"))}>{t("Open Google Flow")}</Button></div>
  <p className="text-xs text-ink-2">{t("Your Google account stays in the browser. Will It Run does not store Google credentials or call a paid video API. Check the credit cost in Flow before generating.")}</p>
  {(error||status.error)&&<p role="alert" className="text-sm text-bad">{error||status.error}</p>}
  {status.data&&!status.data.tools_ready&&<p role="alert" className="text-sm text-warn">{t("Local FFmpeg and FFprobe are required.")}</p>}
  <div className="grid gap-5 xl:grid-cols-2"><div className="grid content-start gap-3"><h3 className="font-semibold">{t("1. Prepare the shot")}</h3>
   <label className="grid gap-1 text-sm">{t("Shot name")}<input className={field} value={name} onChange={e=>setName(e.target.value)} maxLength={120}/></label>
   {(["first","last"] as const).map(kind=><label className="grid gap-1 text-sm" key={kind}>{(kind==="first"?t("First frame path"):t("Last frame path"))}<span className="flex gap-2"><input className={field} value={kind==="first"?first:last} onChange={e=>(kind==="first"?setFirst:setLast)(e.target.value)}/>{inTauri&&<Button aria-label={(kind==="first"?t("Choose first frame"):t("Choose last frame"))} icon={<FolderOpen size={16}/>} onClick={()=>void action(()=>choose(kind))}/>}</span></label>)}
   <label className="grid gap-1 text-sm">{t("Video prompt")}<textarea rows={5} className={field} value={prompt} onChange={e=>setPrompt(e.target.value)} maxLength={10000}/></label>
   <Button variant="primary" className="justify-self-start" loading={busy} disabled={!name.trim()||!first||!last||!prompt.trim()||!status.data?.tools_ready} onClick={()=>void action(async()=>{const s=await api.post<Session>("/api/videos/flow",{name,first,last,prompt});setSelected(s.id);})}>{t("Prepare for Flow")}</Button>
  </div><div className="grid content-start gap-3"><h3 className="font-semibold">{t("2. Generate in Flow")}</h3>
   <p className="text-sm text-ink-2">{t("In Flow choose Video, Frames, 16:9 and one output. Upload the prepared first and last images, paste the prompt, choose your model and generate using your included credits.")}</p>
   <label className="grid gap-1 text-sm">{t("Prepared shots")}<select className={field} value={session?.id??""} onChange={e=>{setSelected(e.target.value);setVideo("");}}><option value="" disabled>{t("Choose a shot")}</option>{status.data?.sessions.map(s=><option value={s.id} key={s.id}>{s.name} · {labels[s.status as keyof typeof labels]??s.status}</option>)}</select></label>
   {session&&<><div className="break-all rounded-md bg-bg p-3 text-xs leading-6"><p>{t("First frame")}: {session.first}</p><p>{t("Last frame")}: {session.last}</p></div><Button className="justify-self-start" icon={<Copy size={15}/>} onClick={()=>void action(async()=>{await navigator.clipboard.writeText(session.prompt);setNotice(t("Prompt copied."));})}>{t("Copy video prompt")}</Button></>}
   <h3 className="mt-2 font-semibold">{t("3. Import and extract 150 frames")}</h3><p className="text-sm text-ink-2">{t("Download the MP4 from Flow and choose it here. The ZIP includes 150 WebP frames and a manifest for a scroll-linked canvas, up to 1280 × 720.")}</p>
   <label className="grid gap-1 text-sm">{t("Downloaded MP4 path")}<span className="flex gap-2"><input className={field} value={video} onChange={e=>setVideo(e.target.value)}/>{inTauri&&<Button aria-label={t("Choose downloaded video")} icon={<FolderOpen size={16}/>} onClick={()=>void action(()=>choose("video"))}/>}</span></label>
   <div className="flex flex-wrap gap-2"><Button loading={busy||session?.status==="processing"} disabled={!session||!video||!status.data?.tools_ready||session.status==="processing"} onClick={()=>void action(()=>api.post(`/api/videos/flow/${session!.id}/import`,{path:video}))}>{t("Import video and extract frames")}</Button><Button icon={<Download size={16}/>} disabled={session?.status!=="ready"||busy} onClick={()=>void action(download)}>{t("Save frame sequence")}</Button></div>
   {session?.error&&<p role="alert" className="text-sm text-bad">{session.error}</p>}{session&&<p role="status" className="text-sm">{labels[session.status as keyof typeof labels]??session.status}{session.status==="ready"?` · ${session.frames} ${t("frames")}`:""}</p>}
  </div></div>{notice&&<p role="status" className="break-all text-sm text-ok">{notice}</p>}
 </section>;
}
