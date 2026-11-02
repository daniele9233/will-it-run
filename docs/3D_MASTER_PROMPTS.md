# Qwen3.5 3D Coder — two scene prompts

Prepared 2026-11-01 for Will It Run. These prompts have not been sent to the owner's model. They generate procedural Three.js HTML scenes; they are not TRELLIS image reconstruction prompts.

## Research and compatibility

- [Author's model card](https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder): independent experimental fine-tune, Three.js core-only examples, custom camera controls, temperature 0.6–0.8, top-p 0.90–0.95, top-k 20–40, repeat penalty 1.0; disable MTP in LM Studio. It also documents duplicate variable names as an observed failure.
- [Author's training dataset](https://huggingface.co/datasets/ccore/ThreeJS-3D-Scenes): English scene descriptions covering mechanisms, individual objects, architecture and landscapes. The detailed subjects below are original creative specifications, not copies of those examples.
- [Three.js PBR materials](https://threejs.org/docs/pages/MeshStandardMaterial.html): use meaningful metallic/roughness values and environment lighting.
- [Three.js PMREM](https://threejs.org/docs/pages/PMREMGenerator.html): create an environment from a locally constructed scene rather than downloading an HDR image.
- [Three.js shadows](https://threejs.org/manual/pages/shadows.html): limit shadow-casting lights and size the shadow camera to the subject.

The author's examples import Three.js from a CDN. Will It Run already embeds Three.js as global `THREE` and forbids external requests, so the following prompts deliberately use that local runtime. They request neither addons nor external models, fonts, textures or scripts. Current app sampling: temperature 0.7, top-p 0.95, top-k 20, repeat penalty 1.0, explicit MTP-off load.

## Prompt 1 — KAZE-01, mechanical samurai collectible

Create a complete, self-contained HTML document that presents KAZE-01: an original mechanical samurai collectible, crafted with the visual care of a premium museum display. Aim for a coherent, beautiful, richly constructed 3D object rather than a pile of generic primitives.

RUNTIME CONTRACT
Three.js is already available as the global THREE object. Use core Three.js only. Do not import anything, do not load URLs, and do not use addons, external models, image files, fonts, libraries or postprocessing passes. Generate all geometry and optional CanvasTexture maps inside the document. Implement orbit, zoom and camera reset directly. Return only the finished HTML, without Markdown or explanation.

ART DIRECTION
Build a full-body, adult-proportioned humanoid robot in a calm, balanced standing pose on a low circular graphite display plinth. Its silhouette must immediately read as a samurai: a compact kabuto helmet, two elegant sturdy horns, a recessed face visor, layered shoulder armor, a fitted cuirass, overlapping waist plates and armored shins. Use warm ivory ceramic armor, charcoal titanium mechanisms, desaturated teal secondary panels, restrained copper fittings and deep red braided cords. Keep the whole figure visible in the initial three-quarter view, with clear space around its helmet and feet.

CONSTRUCTION AND DETAIL
Create a deliberate hierarchy of THREE.Group objects: torso, helmet, shoulder armor, articulated arms, pelvis and legs. Establish convincing proportions and joints before adding small details. Use shaped or extruded panels for major armor, not merely spheres placed on boxes. Give armor plates thickness, gently rounded or beveled edges and visible overlap. Build the helmet brim and crest as geometry. Add recessed visor framing, layered neck protection, elbow and knee hinges, piston-like forearm parts, mechanical three-finger hands and segmented boot soles. Include a sheathed katana attached securely to the belt. Add restrained panel seams, fasteners, vent slats and cable guides where they make structural sense. The back and sides must be designed too, not left blank. Reuse materials and geometry; instance repeated fasteners when useful.

MATERIALS AND LIGHTING
Use physically based materials with genuinely different roughness and metalness for ceramic, painted metal, exposed metal, rubber and cloth-like cords. Fine procedural surface variation should be subtle and must not replace geometry. Give the visor a small, controlled emissive accent, without washing out the face. Construct a simple studio environment in code and prefilter it with THREE.PMREMGenerator; do not import RoomEnvironment. Use a soft-looking warm key, cool fill and restrained rim. Enable ground contact shadows using one tightly framed shadow-casting key light and a high-resolution shadow map. Use antialiasing, sRGB output and ACES filmic tone mapping with balanced exposure. Preserve detail in both ivory highlights and dark joints.

PRESENTATION AND INTERACTION
Use an unobtrusive dark studio background. The collectible must dominate the canvas. Provide compact Italian controls: “Rotazione”, “Vista esplosa” and “Ripristina”. The exploded view should separate logical armor groups a short distance from their stored original positions, then restore them accurately. Keep the inner robot coherent. Make motion time-based, smooth and reversible; pause automatic rotation during user manipulation and respect reduced motion. Resize the renderer and camera correctly when the window changes size. Keep controls outside the model's silhouette.

QUALITY CHECK BEFORE OUTPUT
Check that names are unique, objects are defined before use, group transforms are consistent, the initial camera sees the entire figure, shadows reach the plinth, and all buttons work without dependencies. Prioritize a complete, working, carefully lit model over extra features. Do not substitute images or flat billboards for the character. Output the complete HTML only.

## Prompt 2 — The Cloud Sanctuary, Japanese garden diorama

Create one complete, self-contained HTML document showing The Cloud Sanctuary: a sophisticated Japanese-inspired architectural diorama on a floating rocky island, with the craftsmanship of a miniature built for a cinematic exhibition. Make its spatial composition, material richness and quiet atmosphere immediately striking.

RUNTIME CONTRACT
The global THREE object is already supplied by the host. Use Three.js core APIs only. No imports, addons, network requests, external images, models, HDR maps, fonts or libraries. Construct all geometry and any CanvasTexture maps procedurally. Implement camera orbit, zoom and reset yourself. Return only the complete HTML document, without Markdown or commentary.

COMPOSITION
Design three complementary focal points: a finely detailed timber sanctuary on the upper terrace, a vermilion arched bridge over a narrow pond in the foreground, and a sculpted flowering cherry tree balancing the opposite side. Support everything on a convincing layered rock island with roots and a tapered underside. Connect the terraces with stone steps and a readable path. Use a shallow three-quarter initial view that reveals the bridge, pavilion, pond and island underside together. Leave enough margin to orbit without clipping the roofs or tree. Keep the diorama self-contained, with complete side and rear geometry.

ARCHITECTURE
Give the sanctuary a raised stone foundation, visible timber posts and brackets, recessed sliding doors, small lattice screens and a layered roof with curved eaves. Construct roof surfaces and trim with shaped geometry or extruded profiles instead of stacking generic boxes. Use dark blue-gray roof tiles, weathered cedar, restrained vermilion details and small aged-brass ornaments. Add meaningful secondary detail: roof ribs, a narrow veranda, railings with joints, carved stepping stones, two stone lanterns and a compact torii along the approach. Paths, stairs and bridge ends must actually meet; supports must touch their foundations.

GARDEN AND WATER
Model the cherry tree with a coherent tapered trunk, curved branches and clustered three-dimensional blossom masses, using varied small petal instances rather than a flat pink sphere. Put moss, a few grass clumps and smooth stones in intentional groups. Build the pond with visible depth, a dark bed, shoreline stones and a restrained reflective or translucent water surface. A small spillway should feed a thin waterfall down the island edge. Animate gentle ripples or falling droplets procedurally without importing Water or shader addons. Keep water motion and a few drifting petals delicate; they must not obscure the architecture. Use a deterministic random generator for repeatable decorative placement.

LIGHT AND MATERIALS
Create a refined late-afternoon palette: warm low sunlight on timber and blossom edges, cooler open-sky fill in shaded stone and a subtle warm glow inside the pavilion. Distinguish wood, stone, moss, painted timber, brass and water with physically based materials and sensible roughness/metalness. Add restrained procedural grain or surface variation where it helps. Create a local environment in code and use THREE.PMREMGenerator for coherent reflections. Use one principal shadow-casting light with a tightly bounded high-resolution shadow map, soft fill lights, antialiasing, sRGB output and ACES filmic tone mapping. Preserve material color and shadow detail; avoid excessive fog, blown highlights or unsupported bloom effects.

INTERACTION AND FINISH
Keep the diorama large and centered in a responsive full-window canvas with a quiet atmospheric background and minimal Italian controls: “Movimento”, “Giorno / Tramonto” and “Ripristina vista”. Switching light moods should preserve the geometry and camera. Implement smooth, frame-rate-independent motion and pause it for reduced-motion preferences. Users must be able to orbit all the way around, inspect the bridge and roof details, zoom in and return to the initial framing. Keep repeated geometry efficient without making the scene visibly crude.

QUALITY CHECK BEFORE OUTPUT
Verify scene scale, object names, branch and roof construction, water placement, contact shadows, camera clipping planes, responsive sizing and the control handlers. Favor elegant hierarchy, actual three-dimensional forms and a polished first frame over unrelated decorative clutter. No placeholder objects, images standing in for scenery, unfinished functions or missing dependencies. Output only the finished HTML.
