package main

import (
	"fmt"
	"os"
	"strconv"
	"sync"
)

func work(n, seed int64) int64 {
	s := seed
	for i := int64(0); i < n; i++ {
		s = (s*1103515245 + 12345) % 2147483648
	}
	return s
}

func main() {
	mode := os.Args[1]
	switch mode {
	case "spawn":
		n, _ := strconv.ParseInt(os.Args[2], 10, 64)
		var s int64
		for i := int64(0); i < n; i++ {
			ch := make(chan int64)
			go func(x int64) { ch <- x + 1 }(i)
			s += <-ch
		}
		fmt.Println(s)
	case "split":
		k, _ := strconv.Atoi(os.Args[2])
		total, _ := strconv.ParseInt(os.Args[3], 10, 64)
		res := make([]int64, k)
		var wg sync.WaitGroup
		for i := 0; i < k; i++ {
			wg.Add(1)
			go func(i int) { defer wg.Done(); res[i] = work(total/int64(k), int64(i+1)) }(i)
		}
		wg.Wait()
		var s int64
		for _, r := range res {
			s += r
		}
		fmt.Println(s)
	case "chan":
		m, _ := strconv.ParseInt(os.Args[2], 10, 64)
		ch := make(chan int64, 1) // capacity 1 == Kol's single-slot channel
		done := make(chan int64)
		go func() { for i := int64(0); i < m; i++ { ch <- i } }()
		go func() { var s int64; for i := int64(0); i < m; i++ { s += <-ch }; done <- s }()
		fmt.Println(<-done)
	case "fanout":
		n, _ := strconv.ParseInt(os.Args[2], 10, 64)
		res := make([]int64, n)
		var wg sync.WaitGroup
		for i := int64(0); i < n; i++ {
			wg.Add(1)
			go func(i int64) { defer wg.Done(); res[i] = i + 1 }(i)
		}
		wg.Wait()
		var s int64
		for _, r := range res {
			s += r
		}
		fmt.Println(s)
	case "chanbuf":
		m, _ := strconv.ParseInt(os.Args[2], 10, 64)
		ch := make(chan int64, 1024)
		done := make(chan int64)
		go func() { for i := int64(0); i < m; i++ { ch <- i } }()
		go func() { var s int64; for i := int64(0); i < m; i++ { s += <-ch }; done <- s }()
		fmt.Println(<-done)
	case "chan0":
		m, _ := strconv.ParseInt(os.Args[2], 10, 64)
		ch := make(chan int64) // idiomatic unbuffered
		done := make(chan int64)
		go func() { for i := int64(0); i < m; i++ { ch <- i } }()
		go func() { var s int64; for i := int64(0); i < m; i++ { s += <-ch }; done <- s }()
		fmt.Println(<-done)
	}
}
