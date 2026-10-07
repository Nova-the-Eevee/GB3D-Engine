ifndef GBDK_HOME
	GBDK_HOME = C:\gbdk
endif

LCC = $(GBDK_HOME)/bin/lcc
PROJECTNAME = game
BINS = $(PROJECTNAME).gb
CSOURCES := $(wildcard *.c)
ASMSOURCES := $(wildcard *.s)
LCCFLAGS += -msm83:gb -Wf--max-allocs-per-node50000 -Wf--opt-code-speed -Wm-yC -Wm-yn"GB3D"

all: $(BINS)

$(BINS): $(CSOURCES) $(ASMSOURCES)
	$(LCC) $(LCCFLAGS) -o $@ $(CSOURCES) $(ASMSOURCES)

clean:
	rm -f *.o *.lst *.map *.gb *.ihx *.sym *.cdb *.adb *.asm *.noi *.rst
