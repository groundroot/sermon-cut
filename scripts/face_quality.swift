// Score still frames for a thumbnail: Apple Vision face capture quality (0–1, sharpness/pose/eyes/expression)
// and the face size. Used by sermon_cut.py thumbs; compiled once to ~/.cache/video-editor/face_quality.
// Run with: face_quality <img> [<img> ...]  -> one JSON line per image: {"file", "faces", "quality", "area", "cx", "cy"}
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    var out: [String: Any] = ["file": path, "faces": 0, "quality": 0.0, "area": 0.0, "cx": 0.0, "cy": 0.0]
    if let img = NSImage(contentsOfFile: path),
       let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) {
        let req = VNDetectFaceCaptureQualityRequest()
        let handler = VNImageRequestHandler(cgImage: cg, options: [:])
        try? handler.perform([req])
        let faces = req.results ?? []
        out["faces"] = faces.count
        // the largest face is the preacher (the screen behind may show small faces)
        if let f = faces.max(by: { $0.boundingBox.width * $0.boundingBox.height < $1.boundingBox.width * $1.boundingBox.height }) {
            out["quality"] = Double(f.faceCaptureQuality ?? 0)
            out["area"] = Double(f.boundingBox.width * f.boundingBox.height)
            out["cx"] = Double(f.boundingBox.midX)
            out["cy"] = Double(1 - f.boundingBox.midY)
        }
    }
    if let d = try? JSONSerialization.data(withJSONObject: out), let s = String(data: d, encoding: .utf8) { print(s) }
}
