# Script: Add prompt panel to smart canvas preview modal

$smartCanvasHtmlPath = "E:\running hub\Infinite-Canvas-main\static\smart-canvas.html"
$smartCanvasJsPath = "E:\running hub\Infinite-Canvas-main\static\js\smart-canvas.js"

# Backup files
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item -Path $smartCanvasHtmlPath -Destination "$smartCanvasHtmlPath.bak-$timestamp" -Force
Copy-Item -Path $smartCanvasJsPath -Destination "$smartCanvasJsPath.bak-$timestamp" -Force
Write-Host "Backups created" -ForegroundColor Green

# 1. Add prompt panel HTML
$htmlContent = Get-Content -Path $smartCanvasHtmlPath -Raw
$oldHtml = '<span id="previewMetaHint" class="image-edit-sub" style="margin-left:auto"></span>'
$newHtml = @'
<span id="previewMetaHint" class="image-edit-sub" style="margin-left:auto"></span>
                    </div>
                    <div id="outputPromptPanel" class="output-prompt-panel" style="display:none; margin-top:8px; padding:8px 12px; background:rgba(0,0,0,0.05); border-radius:6px; max-height:120px; overflow-y:auto;">
                        <div id="outputPromptText" class="output-prompt-text" style="font-size:13px; line-height:1.5; color:#374151; white-space:pre-wrap; word-break:break-word;"></div>
                        <div class="output-prompt-actions" style="margin-top:6px; display:flex; gap:8px;">
                            <button id="outputCopyPromptBtn" class="preview-text-btn secondary" type="button" style="font-size:12px; padding:4px 8px; border:1px solid #d1d5db; border-radius:4px; background:white; cursor:pointer;">
                                <i data-lucide="copy" style="width:14px; height:14px;"></i> 复制提示词
                            </button>
                        </div>
                    </div>
'@

$htmlContent = $htmlContent.Replace($oldHtml, $newHtml)
Set-Content -Path $smartCanvasHtmlPath -Value $htmlContent -NoNewline -Encoding UTF8
Write-Host "HTML updated" -ForegroundColor Green

# 2. Update JS
$jsContent = Get-Content -Path $smartCanvasJsPath -Raw

$oldJs = 'function updatePreviewMetaHint(extraText=previewMetaExtraText){'
$newJs = @'
function setupSmartOutputPromptPanel(node){
    const panel = document.getElementById('outputPromptPanel');
    const textEl = document.getElementById('outputPromptText');
    const copyBtn = document.getElementById('outputCopyPromptBtn');
    if(!panel || !textEl) return;
    const prompt = node?.runPrompt || node?.promptDraftText || '';
    if(prompt){
        panel.style.display = 'block';
        textEl.textContent = prompt;
        if(copyBtn){
            copyBtn.onclick = () => {
                navigator.clipboard.writeText(prompt).then(() => {
                    if(typeof toast === 'function') toast('提示词已复制');
                });
            };
        }
    } else {
        panel.style.display = 'none';
    }
}
function updatePreviewMetaHint(extraText=previewMetaExtraText){
'@

$jsContent = $jsContent.Replace($oldJs, $newJs)

# Add setupSmartOutputPromptPanel call in openImageEditor
$oldOpen = "setImageEditMode('preview');
    updatePreviewNavButtons();"
$newOpen = "setImageEditMode('preview');
    setupSmartOutputPromptPanel(node);
    updatePreviewNavButtons();"
$jsContent = $jsContent.Replace($oldOpen, $newOpen)

Set-Content -Path $smartCanvasJsPath -Value $jsContent -NoNewline -Encoding UTF8
Write-Host "JS updated" -ForegroundColor Green
Write-Host "Done! Restart server and refresh browser." -ForegroundColor Cyan
