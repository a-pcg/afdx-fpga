#include "Vtb_afdx_baseline.h"
#include "verilated.h"
int main(int argc,char**argv){VerilatedContext c;c.commandArgs(argc,argv);Vtb_afdx_baseline top{&c};
while(!c.gotFinish()){top.eval();if(!top.eventsPending())break;c.time(top.nextTimeSlot());}
top.final();return c.gotFinish()?0:1;}
