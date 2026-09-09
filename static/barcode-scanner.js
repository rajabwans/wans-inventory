(function(){'use strict';
var videoStream=null,videoEl=null,scanning=false,detector=null,scanInterval=null;
function createVideo(c){
    videoEl=document.createElement('video');
    videoEl.setAttribute('playsinline','');videoEl.setAttribute('autoplay','');
    videoEl.style.width='100%';videoEl.style.borderRadius='12px';videoEl.style.background='#000';
    c.appendChild(videoEl);return videoEl;
}
async function startCamera(c,onResult){
    try{
        videoEl=createVideo(c);
        var stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment',width:{ideal:1280},height:{ideal:720}}});
        videoStream=stream;videoEl.srcObject=stream;await videoEl.play();scanning=true;
        if('BarcodeDetector' in window){
            detector=new BarcodeDetector({formats:['ean_13','ean_8','code_128','code_39','upc_a','upc_e']});
            scanInterval=setInterval(function(){if(!scanning)return;detector.detect(videoEl).then(function(bars){
                if(bars.length>0){stop();onResult(bars[0].rawValue);}
            }).catch(function(){});},250);
        }else{
            var canvas=document.createElement('canvas');var ctx=canvas.getContext('2d');
            scanInterval=setInterval(function(){
                if(!scanning||!videoEl)return;
                canvas.width=videoEl.videoWidth;canvas.height=videoEl.videoHeight;
                ctx.drawImage(videoEl,0,0);
            },300);
            setTimeout(function(){stop();alert('Your browser does not support camera barcode scanning. Enter the barcode manually below.');},1500);
        }
    }catch(e){console.error(e);alert('Could not access camera. Enter the barcode manually.');}
}
function stop(){scanning=false;if(scanInterval){clearInterval(scanInterval);scanInterval=null;}
    if(videoStream){videoStream.getTracks().forEach(function(t){t.stop();});videoStream=null;}
    if(videoEl){videoEl.remove();videoEl=null;}}
function torch(){
    if(!videoStream)return;
    var track=videoStream.getVideoTracks()[0];if(!track)return;
    var caps=track.getCapabilities?track.getCapabilities():{};
    if(caps.torch){var s=track.getSettings?track.getSettings():{};track.applyConstraints({advanced:[{torch:!s.torch}]});}
}
window.WanScan={start:startCamera,stop:stop,torch:torch,isScanning:function(){return scanning;}};
})();
