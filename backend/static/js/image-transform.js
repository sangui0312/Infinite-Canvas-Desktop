/* Shared pixel transforms for both canvas editors. */
function transformImageCanvas(source, action){
    const rotated = action === 'left' || action === 'right';
    const next = document.createElement('canvas');
    next.width = rotated ? source.height : source.width;
    next.height = rotated ? source.width : source.height;
    const ctx = next.getContext('2d');
    ctx.translate(next.width / 2, next.height / 2);
    if(action === 'left') ctx.rotate(-Math.PI / 2);
    if(action === 'right') ctx.rotate(Math.PI / 2);
    ctx.scale(action === 'horizontal' ? -1 : 1, action === 'vertical' ? -1 : 1);
    ctx.drawImage(source, -source.width / 2, -source.height / 2);
    return next;
}
