(function(){'use strict';
var characteristic=null;
function strToBytes(s){return new TextEncoder().encode(s);}
function enc(){return new Uint8Array([0x1B,0x40]);}
function alignCenter(){return new Uint8Array([0x1B,0x61,1]);}
function alignLeft(){return new Uint8Array([0x1B,0x61,0]);}
function bold(on){return new Uint8Array([0x1B,0x45,on?1:0]);}
function textSize(w,h){return new Uint8Array([0x1D,0x21,((h&0xF)<<4)|(w&0xF)]);}
function feed(n){return new Uint8Array([0x1B,0x64,n]);}
function cut(){return new Uint8Array([0x1D,0x56,0x30]);}
function line(len){return strToBytes('\u2500'.repeat(len||32)+'\n');}
function join(a,b){var c=new Uint8Array(a.length+b.length);c.set(a);c.set(b,a.length);return c;}
async function connect(){
    if(!navigator.bluetooth){alert('Web Bluetooth is not supported in this browser. Use Chrome on Android.');return false;}
    try{
        var device=await navigator.bluetooth.requestDevice({filters:[{services:['generic_access']},{namePrefix:'POS'},{namePrefix:'TM'},{namePrefix:'GP'},{namePrefix:'BT'}],optionalServices:['generic_access']});
        var server=await device.gatt.connect();
        var characteristics=[];
        try{var s=await server.getPrimaryService('generic_access');characteristics=await s.getCharacteristics();}catch(e){}
        characteristic=characteristics.find(function(c){return c.properties.write||c.properties.writeWithoutResponse;});
        if(!characteristic){
            var services=await server.getPrimaryServices();
            for(var i=0;i<services.length;i++){
                var chs=await services[i].getCharacteristics();
                var w=chs.find(function(c){return c.properties.write||c.properties.writeWithoutResponse;});
                if(w){characteristic=w;break;}
            }
        }
        if(!characteristic){alert('Could not find a writable characteristic on the printer.');return false;}
        return true;
    }catch(e){console.error(e);alert('Could not connect: '+e.message);return false;}
}
async function send(data){
    if(!characteristic)return false;
    try{
        var size=256;
        for(var i=0;i<data.length;i+=size){
            var chunk=data.slice(i,i+size);
            await characteristic.writeValueWithoutResponse(chunk).catch(function(){return characteristic.writeValue(chunk);});
        }
        return true;
    }catch(e){console.error('Print error',e);return false;}
}
async function printReceipt(data){
    var connected=await connect();if(!connected)return false;
    data=data||{};
    var cmd=new Uint8Array(0);
    cmd=join(cmd,enc());
    cmd=join(cmd,alignCenter());cmd=join(cmd,bold(true));cmd=join(cmd,textSize(1,1));
    cmd=join(cmd,strToBytes((data.business_name||'WANPLAN')+'\n'));
    cmd=join(cmd,textSize(0,0));cmd=join(cmd,bold(false));
    if(data.address)cmd=join(cmd,strToBytes(data.address+'\n'));
    if(data.phone)cmd=join(cmd,strToBytes('Tel: '+data.phone+'\n'));
    cmd=join(cmd,line());cmd=join(cmd,alignCenter());cmd=join(cmd,bold(true));
    cmd=join(cmd,strToBytes((data.title||'RECEIPT')+'\n'));cmd=join(cmd,bold(false));
    cmd=join(cmd,strToBytes('#'+(data.receipt_no||'')+'\n'));
    cmd=join(cmd,strToBytes((data.date||new Date().toLocaleDateString())+'\n'));
    cmd=join(cmd,line());cmd=join(cmd,alignLeft());
    var currency=data.currency||'UGX';
    if(data.items&&data.items.length){
        for(var i=0;i<data.items.length;i++){
            var it=data.items[i];var name=(it.name||'').substring(0,20);
            var qty=it.qty||1;var price=it.price||0;var tot=qty*price;
            var left=name+' x'+qty;var right=currency+' '+tot.toLocaleString();
            var pad=32-left.length-right.length;
            cmd=join(cmd,strToBytes(left+' '.repeat(Math.max(1,pad))+right+'\n'));
        }
    }else if(data.product_name){
        var L=data.product_name;var R=currency+' '+(data.total||0).toLocaleString();
        var P=32-L.length-R.length;
        cmd=join(cmd,strToBytes(L+' '.repeat(Math.max(1,P))+R+'\n'));
        if(data.qty)cmd=join(cmd,strToBytes('Qty: '+data.qty+' @ '+currency+' '+(data.unit_price||0).toLocaleString()+'\n'));
    }
    cmd=join(cmd,line());cmd=join(cmd,bold(true));
    var tl='TOTAL';var trr=currency+' '+(data.total||0).toLocaleString();
    var tp=32-tl.length-trr.length;
    cmd=join(cmd,strToBytes(tl+' '.repeat(Math.max(1,tp))+trr+'\n'));
    cmd=join(cmd,bold(false));
    if(data.customer_name)cmd=join(cmd,strToBytes('Customer: '+data.customer_name+'\n'));
    cmd=join(cmd,line());cmd=join(cmd,alignCenter());
    cmd=join(cmd,strToBytes('Thank you!\n'));cmd=join(cmd,strToBytes('Powered by '+(data.product_name||'WANPLAN')+'\n'));
    cmd=join(cmd,feed(3));cmd=join(cmd,cut());
    return await send(cmd);
}
function disconnect(){characteristic=null;}
window.WanPrint={connect:connect,printReceipt:printReceipt,disconnect:disconnect,isConnected:function(){return !!characteristic;}};
})();
