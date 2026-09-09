(function(){'use strict';
var loaded=false;
function load(cb){
    if(loaded){cb();return;}
    var s=document.createElement('script');
    s.src='https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js';
    s.onload=function(){loaded=true;cb();};
    document.head.appendChild(s);
}
function profit(id,data){
    load(function(){
        var ctx=document.getElementById(id);if(!ctx)return;
        new Chart(ctx,{type:'line',data:{labels:data.labels,datasets:[
            {label:'Revenue',data:data.revenue,borderColor:'#4f46e5',backgroundColor:'rgba(79,70,229,0.1)',fill:true,tension:0.3},
            {label:'Profit',data:data.profit,borderColor:'#10b981',backgroundColor:'rgba(16,185,129,0.1)',fill:true,tension:0.3},
            {label:'Expenses',data:data.expenses,borderColor:'#ef4444',backgroundColor:'rgba(239,68,68,0.1)',fill:true,tension:0.3}]},
            options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'top'},tooltip:{callbacks:{label:function(c){return c.dataset.label+': '+(data.currency||'UGX')+' '+c.parsed.y.toLocaleString();}}}},
            scales:{y:{beginAtZero:true,ticks:{callback:function(v){return (data.currency||'UGX')+' '+v.toLocaleString();}}}}}});
    });
}
function category(id,data){
    load(function(){
        var ctx=document.getElementById(id);if(!ctx)return;
        var colors=['#4f46e5','#7c3aed','#06b6d4','#10b981','#f59e0b','#14b8a6','#f43f5e','#64748b'];
        new Chart(ctx,{type:'doughnut',data:{labels:data.labels,datasets:[{data:data.values,backgroundColor:colors.slice(0,data.labels.length),borderWidth:2,borderColor:'#fff'}]},
            options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom'},tooltip:{callbacks:{label:function(c){
                var total=c.dataset.data.reduce(function(a,b){return a+b;},0);
                var pct=((c.parsed/total)*100).toFixed(1);
                return c.label+': '+(data.currency||'UGX')+' '+c.parsed.toLocaleString()+' ('+pct+'%)';}}}}}});
    });
}
function bar(id,data){
    load(function(){
        var ctx=document.getElementById(id);if(!ctx)return;
        new Chart(ctx,{type:'bar',data:{labels:data.labels,datasets:[{label:data.datasetLabel||'Amount',data:data.values,backgroundColor:data.colors||'#4f46e5',borderRadius:6}]},
            options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:function(c){return (data.currency||'UGX')+' '+c.parsed.y.toLocaleString();}}}},
            scales:{y:{beginAtZero:true,ticks:{callback:function(v){return (data.currency||'UGX')+' '+v.toLocaleString();}}}}}});
    });
}
window.WanChart={profit:profit,category:category,bar:bar};
})();
